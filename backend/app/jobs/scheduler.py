"""APScheduler entry point — started inside the FastAPI process at app startup."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.jobs.daily_brief import run_for_all_users
from app.jobs.health_check import run as health_check
from app.jobs.sync import sync_all

log = logging.getLogger("coach.scheduler")
_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    s = BackgroundScheduler(timezone="UTC")
    s.add_job(sync_all, IntervalTrigger(minutes=30), id="sync_all", max_instances=1)
    # Hourly, not daily. The job itself decides who is due, by checking whether
    # it is currently the target hour in each user's own timezone — a single
    # fixed UTC time delivered a "morning" brief at 3am in California.
    s.add_job(
        run_for_all_users,
        CronTrigger(minute=5),
        id="daily_brief",
        max_instances=1,
    )
    # 18:00 UTC — early afternoon in the US, evening in Europe, so every
    # timezone is past its 11am brief deadline and a missing brief is a real
    # failure rather than one that has simply not happened yet.
    s.add_job(
        health_check,
        CronTrigger(hour=18, minute=0),
        id="health_check",
        max_instances=1,
    )
    s.start()
    log.info("scheduler started: sync every 30m, brief check hourly, health check daily")
    _scheduler = s
    return s


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
