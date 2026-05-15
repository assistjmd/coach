"""APScheduler entry point — started inside the FastAPI process at app startup."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.jobs.daily_brief import run_for_all_users
from app.jobs.sync import sync_all

log = logging.getLogger("coach.scheduler")
_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    s = BackgroundScheduler(timezone="UTC")
    s.add_job(sync_all, IntervalTrigger(minutes=30), id="sync_all", max_instances=1)
    s.add_job(run_for_all_users, CronTrigger(hour=10, minute=0), id="daily_brief", max_instances=1)
    s.start()
    log.info("scheduler started: sync every 30m, daily brief at 10:00 UTC")
    _scheduler = s
    return s


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
