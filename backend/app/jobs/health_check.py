"""Daily check on the things that fail silently.

Each of these has already happened. Briefs stopped for two days when the API
balance ran out; every OAuth connection broke for a day when a provider change
pointed the sync job at a placeholder. Neither moved a number anyone was looking
at, and both were found by chance.

Sends only when something is wrong. A daily all-clear is an email nobody reads,
and the point is that a message arriving means something.
"""

from __future__ import annotations

import logging

from app import alerts
from app.analytics.product import collect
from app.db import SessionLocal

log = logging.getLogger("coach.health")


def run() -> list[str]:
    """Check, alert if needed, and return the problems found."""
    with SessionLocal() as db:
        m = collect(db).as_dict()

    problems: list[str] = []

    gap = m.get("coaching", {}).get("missing_brief_today") or {}
    if gap.get("all_missing"):
        problems.append(
            f"No briefs generated today. {gap['missing']} of {gap['of']} accounts "
            "with data have nothing. Usually an exhausted API balance or an "
            "expired key."
        )

    erroring = m.get("sources", {}).get("connections_erroring") or 0
    connections = sum((m.get("sources", {}).get("oauth_connections") or {}).values())
    if erroring and erroring == connections:
        problems.append(
            f"All {erroring} provider connections are failing to sync. "
            "Usually a revoked token or a provider that has been switched off."
        )

    if not problems:
        log.info("health check clear")
        return []

    body = "\n\n".join(problems) + (
        "\n\n---\nRun `python3 tools/metrics.py` for the full picture."
    )
    alerts.send("Something needs looking at", body)
    for p in problems:
        log.error("health check: %s", p)
    return problems
