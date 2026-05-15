"""Per-provider, per-user sync. Runs on a schedule."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    HRVReading,
    OAuthToken,
    ReadinessScore,
    SleepSession,
    Workout,
)
from app.providers import all_providers, get_provider
from app.providers.base import NormalizedRecord
from app.routes.providers import load_tokenset

log = logging.getLogger("coach.sync")


def sync_all() -> None:
    """Iterate every connected OAuth token and pull deltas."""
    with SessionLocal() as db:
        tokens = db.query(OAuthToken).all()
    for tok in tokens:
        try:
            sync_one(tok.id)
        except Exception:
            log.exception("sync failed for token %s (%s)", tok.id, tok.provider)


def sync_one(token_id) -> int:
    written = 0
    with SessionLocal() as db:
        tok = db.get(OAuthToken, token_id)
        if tok is None:
            return 0
        provider = get_provider(tok.provider)
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

        since = tok.last_synced_at
        for rec in provider.sync(ts, since):
            _apply(db, tok.user_id, tok.provider, rec)
            written += 1
        tok.last_synced_at = datetime.now(timezone.utc)
        db.commit()
    return written


def _apply(db: Session, user_id, source: str, rec: NormalizedRecord) -> None:
    p = rec.payload
    if rec.kind == "workout":
        stmt = pg_insert(Workout).values(
            user_id=user_id,
            source=source,
            source_record_id=rec.source_record_id,
            sport=p.get("sport"),
            started_at=p["started_at"],
            ended_at=p["ended_at"],
            duration_s=_duration_s(p),
            distance_m=p.get("distance_m"),
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
        db.add(
            HRVReading(
                user_id=user_id,
                source=source,
                measured_at=p["measured_at"],
                rmssd_ms=p.get("rmssd_ms"),
                sdnn_ms=p.get("sdnn_ms"),
            )
        )
    elif rec.kind == "readiness":
        stmt = pg_insert(ReadinessScore).values(
            user_id=user_id,
            source=source,
            day=p["day"],
            score=p["score"],
            raw=p.get("raw"),
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_readiness_day"))


def _duration_s(p: dict) -> int:
    started = p.get("started_at")
    ended = p.get("ended_at")
    if isinstance(started, str):
        started = datetime.fromisoformat(started.replace("Z", "+00:00"))
    if isinstance(ended, str):
        ended = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    return int((ended - started).total_seconds())
