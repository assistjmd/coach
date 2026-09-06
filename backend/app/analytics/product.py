"""Aggregate product metrics. Counts and rates only, never content.

This exists instead of reading anyone's conversations. The questions worth
answering — is the product working, does anyone come back, is the coaching being
followed — are all answered by counting. Reading five people's messages would
answer none of them, and the privacy policy tells users their conversations are
not something a human sits and reads.

Nothing here selects a message body, a journal entry, or a prescription. If a
future metric seems to need one, it does not: it needs a count of something.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics import compliance as comp
from app.models import (
    ChatMessage,
    DailyBrief,
    DayLog,
    OAuthToken,
    Subscription,
    User,
    Workout,
)


@dataclass
class Metrics:
    generated_at: str
    users: dict = field(default_factory=dict)
    retention: dict = field(default_factory=dict)
    engagement: dict = field(default_factory=dict)
    coaching: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    money: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def collect(db: Session) -> Metrics:
    now = datetime.now(timezone.utc)
    m = Metrics(generated_at=now.isoformat())

    total = db.query(func.count(User.id)).scalar() or 0
    onboarded = (
        db.query(func.count(User.id)).filter(User.onboarded_at.isnot(None)).scalar() or 0
    )
    with_data = (
        db.query(func.count(func.distinct(Workout.user_id))).scalar() or 0
    )
    m.users = {
        "total": total,
        "onboarded": onboarded,
        # Signed in and then never finished setup. The clearest signal of a
        # broken first run, and invisible from anywhere else.
        "stalled_in_onboarding": total - onboarded,
        "with_any_workout": with_data,
        "new_7d": db.query(func.count(User.id)).filter(User.created_at >= _since(7)).scalar() or 0,
        "new_28d": db.query(func.count(User.id)).filter(User.created_at >= _since(28)).scalar() or 0,
    }

    def active(days: int) -> int:
        return (
            db.query(func.count(User.id))
            .filter(User.last_seen_at >= _since(days))
            .scalar()
            or 0
        )

    dau, wau, mau = active(1), active(7), active(28)
    m.retention = {
        "active_1d": dau,
        "active_7d": wau,
        "active_28d": mau,
        # The number that decides whether this is a habit or a novelty. Below
        # about 20% means people try it and drift away.
        "dau_over_wau_pct": round(dau / wau * 100, 1) if wau else None,
        "wau_over_mau_pct": round(wau / mau * 100, 1) if mau else None,
        # Seen once, then not for a fortnight. A null last_seen is deliberately
        # excluded: the column is newer than some accounts, so null means "not
        # observed yet", not "gone". Counting those as dormant would invent
        # churn that never happened.
        "dormant_14d": (
            db.query(func.count(User.id))
            .filter(User.last_seen_at.isnot(None), User.last_seen_at < _since(14))
            .scalar()
            or 0
        ),
        "never_seen_since_tracking_began": (
            db.query(func.count(User.id)).filter(User.last_seen_at.is_(None)).scalar() or 0
        ),
    }

    chat_7d = (
        db.query(func.count(ChatMessage.id))
        .filter(ChatMessage.role == "user", ChatMessage.kind == "chat",
                ChatMessage.created_at >= _since(7))
        .scalar() or 0
    )
    chatters_7d = (
        db.query(func.count(func.distinct(ChatMessage.user_id)))
        .filter(ChatMessage.role == "user", ChatMessage.kind == "chat",
                ChatMessage.created_at >= _since(7))
        .scalar() or 0
    )
    adjust_7d = (
        db.query(func.count(ChatMessage.id))
        .filter(ChatMessage.role == "user", ChatMessage.kind == "adjust",
                ChatMessage.created_at >= _since(7))
        .scalar() or 0
    )
    applied_7d = (
        db.query(func.count(DailyBrief.id))
        .filter(DailyBrief.adjusted_at >= _since(7))
        .scalar() or 0
    )
    logs_7d = (
        db.query(func.count(DayLog.id)).filter(DayLog.created_at >= _since(7)).scalar() or 0
    )
    m.engagement = {
        "chat_messages_7d": chat_7d,
        "users_who_chatted_7d": chatters_7d,
        "chat_per_active_user_7d": round(chat_7d / wau, 1) if wau else None,
        "adjust_requests_7d": adjust_7d,
        "adjust_applied_7d": applied_7d,
        # Requests Will answered without changing anything — a question, or a
        # decline. A high rate means the guardrails are too tight or the copy
        # invites requests it will not honour.
        "adjust_declined_or_answered_pct": (
            round((adjust_7d - applied_7d) / adjust_7d * 100, 1) if adjust_7d else None
        ),
        "day_logs_7d": logs_7d,
    }

    briefs_7d = (
        db.query(func.count(DailyBrief.id))
        .filter(DailyBrief.created_at >= _since(7))
        .scalar() or 0
    )
    rest_7d = (
        db.query(func.count(DailyBrief.id))
        .filter(DailyBrief.created_at >= _since(7), DailyBrief.session_planned.is_(False))
        .scalar() or 0
    )
    m.coaching = {
        "briefs_7d": briefs_7d,
        "rest_days_prescribed_7d": rest_7d,
        # Users with a brief yesterday, against users active in the week. A gap
        # means generation is failing quietly for someone.
        "users_with_brief_7d": (
            db.query(func.count(func.distinct(DailyBrief.user_id)))
            .filter(DailyBrief.created_at >= _since(7))
            .scalar() or 0
        ),
        "compliance_14d": _compliance_mix(db),
        # The alarm. Generation is the one thing that must happen every day, and
        # when it stops - an expired API key, an exhausted balance, a provider
        # outage - nothing else in this dashboard moves. Briefs simply stop
        # being written, which looks identical to a quiet week.
        #
        # Counted against accounts that have data to reason about, since someone
        # who connected nothing is correctly missing a brief.
        "missing_brief_today": _missing_today(db),
    }

    m.sources = {
        "healthkit_ingesting": with_data,
        "oauth_connections": dict(
            db.query(OAuthToken.provider, func.count(OAuthToken.id))
            .group_by(OAuthToken.provider)
            .all()
        ),
        "connections_erroring": (
            db.query(func.count(OAuthToken.id))
            .filter(OAuthToken.last_sync_error.isnot(None))
            .scalar() or 0
        ),
    }

    subs = db.query(Subscription).filter(Subscription.revoked.is_(False)).all()
    paying = sum(
        1 for s in subs
        if s.expires_at and (s.expires_at.replace(tzinfo=timezone.utc) if s.expires_at.tzinfo is None else s.expires_at) > now
    )
    in_trial = (
        db.query(func.count(User.id)).filter(User.trial_ends_at > now).scalar() or 0
    )
    # A null trial_ends_at is an account that has not opened the app since
    # trials existed — the clock starts on first sight. Counting it as expired
    # would report churn for people who were never given a trial.
    not_started = (
        db.query(func.count(User.id)).filter(User.trial_ends_at.is_(None)).scalar() or 0
    )
    lapsed = max(0, total - paying - in_trial - not_started)
    m.money = {
        "paying": paying,
        "in_trial": in_trial,
        "trial_not_started": not_started,
        "trial_lapsed_not_paying": lapsed,
        # Only meaningful once trials have actually run out for a cohort, so it
        # stays null rather than reporting 0% off an empty denominator.
        "trial_to_paid_pct": (
            round(paying / (paying + lapsed) * 100, 1) if (paying + lapsed) else None
        ),
    }
    return m


def _missing_today(db: Session) -> dict:
    """Accounts with health data but no brief for their own today.

    A handful is normal — the job waits for last night's sleep and gives up at
    11am local, so anyone earlier than that in their own morning has not been
    written yet. All of them is an outage.
    """
    from app.dates import today_for

    with_data = {
        row[0]
        for row in db.query(Workout.user_id).distinct()
    }
    missing = 0
    total = 0
    for user in db.query(User).all():
        if user.id not in with_data:
            continue
        total += 1
        exists = (
            db.query(DailyBrief.id)
            .filter(DailyBrief.user_id == user.id, DailyBrief.day == today_for(user))
            .first()
        )
        if exists is None:
            missing += 1
    return {
        "missing": missing,
        "of": total,
        # The number to look at. Everyone missing means generation is down.
        "all_missing": bool(total and missing == total),
    }


def _compliance_mix(db: Session) -> dict:
    """How the last fortnight's prescribed sessions actually went.

    Resolved through the same precedence chain the app uses, so this cannot
    disagree with what an athlete sees on their own screen.
    """
    briefs = (
        db.query(DailyBrief).filter(DailyBrief.created_at >= _since(14)).all()
    )
    if not briefs:
        return {}
    logs = {
        (entry.user_id, entry.day): entry
        for entry in db.query(DayLog).filter(DayLog.created_at >= _since(14))
    }
    mix: dict[str, int] = {}
    for b in briefs:
        log = logs.get((b.user_id, b.day))
        state = comp.resolve(b, log.completed_items if log else None)
        mix[state] = mix.get(state, 0) + 1
    return mix
