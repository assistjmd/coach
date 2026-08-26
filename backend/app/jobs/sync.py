"""Per-provider, per-user sync. Runs on a schedule."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.analytics.dedupe import better, find_duplicate
from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    BodyMetric,
    HeartRateSample,
    HRVReading,
    OAuthStateUse,
    OAuthToken,
    ReadinessScore,
    SleepSession,
    Workout,
)
from app.providers import get_sync_provider
from app.providers.base import NormalizedRecord, ReauthRequired
from app.providers.google_health import AccountNotLinked
from app.routes.providers import load_tokenset

log = logging.getLogger("coach.sync")

# How far back to re-fetch beyond the last watermark, to catch late-arriving data.
SYNC_OVERLAP = timedelta(days=3)
COMMIT_EVERY = 100


def sync_all() -> None:
    """Iterate every connected OAuth token and pull deltas."""
    with SessionLocal() as db:
        tokens = db.query(OAuthToken).all()
    for tok in tokens:
        try:
            sync_one(tok.id)
        except Exception:
            log.exception("sync failed for token %s (%s)", tok.id, tok.provider)
    _purge_oauth_states()


def _purge_oauth_states() -> None:
    """Drop spent OAuth state records once they're older than any live token.

    State tokens expire in 10 minutes (`jwt.STATE_TTL`), so a day-old row can no
    longer match anything the signature check would accept. Piggy-backs on
    `sync_all` rather than earning its own scheduler entry.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        with SessionLocal() as db:
            deleted = (
                db.query(OAuthStateUse).filter(OAuthStateUse.used_at < cutoff).delete()
            )
            db.commit()
        if deleted:
            log.info("purged %d spent oauth states", deleted)
    except Exception:
        log.exception("oauth state purge failed")


def sync_one(token_id) -> int:
    """Pull deltas for one connected provider.

    Failures are recorded on the token rather than only logged: the app shows
    the reason on the Sources screen, so a revoked authorization or an expired
    refresh token says so instead of looking like "no new data".
    """
    try:
        return _sync_one_inner(token_id)
    except Exception as exc:
        with SessionLocal() as db:
            tok = db.get(OAuthToken, token_id)
            if tok is not None:
                # Errors written for the athlete are shown as written. Everything
                # else is type-prefixed, because "400 Bad Request" alone gives a
                # user nothing and the class name is at least a starting point.
                if isinstance(exc, ReauthRequired | AccountNotLinked):
                    tok.last_sync_error = str(exc)[:300]
                else:
                    tok.last_sync_error = f"{type(exc).__name__}: {exc}"[:300]
                db.commit()
        raise


def _sync_one_inner(token_id) -> int:
    written = 0
    with SessionLocal() as db:
        tok = db.get(OAuthToken, token_id)
        if tok is None:
            return 0
        # The real implementation, even for a source we have stopped offering:
        # an existing token still works and its owner still expects their data.
        provider = get_sync_provider(tok.provider)
        if provider.info.auth_kind != "oauth2":
            return 0
        ts = load_tokenset(tok)
        if ts.expires_at and ts.expires_at <= datetime.now(timezone.utc):
            ts = provider.refresh(ts)
            tok.access_token_enc = encrypt(ts.access_token)
            if ts.refresh_token:
                tok.refresh_token_enc = encrypt(ts.refresh_token)
            tok.expires_at = ts.expires_at
            db.commit()

        # Stamp the watermark from before the fetch, not after: anything the
        # provider creates while we're iterating would otherwise fall into the
        # gap and never be picked up.
        sync_start = datetime.now(timezone.utc)

        # Re-fetch a few days behind the watermark rather than resuming exactly
        # at it. Providers backfill: Whoop recovery scores land hours after the
        # cycle they describe, Strava activities get edited and re-uploaded. A
        # strict watermark drops anything whose timestamp precedes the last sync
        # but which only appeared afterwards — permanently, since we'd never ask
        # for that window again. Safe because every kind is now idempotent.
        since = (tok.last_synced_at - SYNC_OVERLAP) if tok.last_synced_at else None

        for rec in provider.sync(ts, since):
            _apply(db, tok.user_id, tok.provider, rec)
            written += 1
            # Commit in batches so a first-connect backfill of several hundred
            # activities isn't one transaction that loses everything on one bad row.
            if written % COMMIT_EVERY == 0:
                db.commit()
        tok.last_synced_at = sync_start
        tok.last_sync_error = None
        db.commit()
    return written


def _apply(db: Session, user_id, source: str, rec: NormalizedRecord) -> None:
    p = rec.payload
    if rec.kind == "workout":
        # The same session reaches us from several sources with a different id
        # each time, so the per-source unique constraint does not catch it. Five
        # copies of one lift meant five times the training load.
        existing = find_duplicate(db, user_id, p["started_at"], p["ended_at"])
        if existing is not None:
            if existing.source == source and existing.source_record_id == rec.source_record_id:
                return  # Same row we already hold; the constraint would no-op anyway.
            candidate = Workout(
                source=source,
                sport=p.get("sport"),
                avg_hr=p.get("avg_hr"),
                max_hr=p.get("max_hr"),
                distance_m=p.get("distance_m"),
            )
            if better(candidate, existing):
                # Overwrite in place rather than delete-and-insert: the row id is
                # what any journal or compliance record points at.
                existing.source = source
                existing.source_record_id = rec.source_record_id
                existing.sport = p.get("sport") or existing.sport
                existing.started_at = p["started_at"]
                existing.ended_at = p["ended_at"]
                existing.duration_s = _duration_s(p)
                existing.distance_m = p.get("distance_m") or existing.distance_m
                existing.avg_hr = p.get("avg_hr") or existing.avg_hr
                existing.max_hr = p.get("max_hr") or existing.max_hr
                existing.raw = p.get("raw") or existing.raw
            return

        stmt = pg_insert(Workout).values(
            user_id=user_id,
            source=source,
            source_record_id=rec.source_record_id,
            sport=p.get("sport"),
            started_at=p["started_at"],
            ended_at=p["ended_at"],
            duration_s=_duration_s(p),
            distance_m=p.get("distance_m"),
            # Heart rate decides whether a session is scored by TRIMP or by the
            # duration fallback. Omitting these silently downgraded every cloud
            # workout to the estimate — the provider was sending them, and they
            # were being dropped between the payload and the row.
            avg_hr=p.get("avg_hr"),
            max_hr=p.get("max_hr"),
            raw=p.get("raw"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_workout_source_id"))
    elif rec.kind == "sleep":
        stmt = pg_insert(SleepSession).values(
            user_id=user_id,
            source=source,
            source_record_id=rec.source_record_id,
            started_at=p["started_at"],
            ended_at=p["ended_at"],
            deep_s=p.get("deep_s"),
            light_s=p.get("light_s"),
            rem_s=p.get("rem_s"),
            awake_s=p.get("awake_s"),
            efficiency=p.get("efficiency"),
            raw=p.get("raw"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_sleep_source_id"))
    elif rec.kind == "hrv":
        # rMSSD and SDNN live in separate columns on purpose — Oura reports the
        # former, Apple Health the latter, and they are not interchangeable.
        stmt = pg_insert(HRVReading).values(
            user_id=user_id,
            source=source,
            measured_at=p["measured_at"],
            rmssd_ms=p.get("rmssd_ms"),
            sdnn_ms=p.get("sdnn_ms"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_hrv_measured_at"))
    elif rec.kind == "readiness":
        stmt = pg_insert(ReadinessScore).values(
            user_id=user_id,
            source=source,
            day=p["day"],
            score=p["score"],
            raw=p.get("raw"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_readiness_day"))
    elif rec.kind == "heart_rate":
        stmt = pg_insert(HeartRateSample).values(
            user_id=user_id,
            source=source,
            measured_at=p["measured_at"],
            bpm=p["bpm"],
            context=p.get("context"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_hr_measured_at"))
    elif rec.kind == "body_metric":
        stmt = pg_insert(BodyMetric).values(
            user_id=user_id,
            source=source,
            metric=p["metric"],
            value=p["value"],
            measured_at=p["measured_at"],
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_body_metric_measured_at"))
    else:
        # Reaching here means a provider emits a kind this function doesn't
        # persist. Silence is how `heart_rate` and `body_metric` were dropped on
        # the floor for as long as they were — a provider looked like it was
        # syncing while its most useful signal went nowhere.
        log.warning("unhandled normalized kind %r from source %s", rec.kind, source)


def _duration_s(p: dict) -> int:
    started = p.get("started_at")
    ended = p.get("ended_at")
    if isinstance(started, str):
        started = datetime.fromisoformat(started.replace("Z", "+00:00"))
    if isinstance(ended, str):
        ended = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    return int((ended - started).total_seconds())
