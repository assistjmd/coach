"""Tell the operator when something is wrong, by email.

Briefs stopped for two days across every account and nothing said so — the
dashboard only counts what was written, and a week where nothing is written
reads as a quiet week. Finding that out by noticing your own workout was missing
does not scale past the people who already know the app well.

Deliberately quiet. This sends only when something is actually broken, because
a daily "all fine" email is an email nobody opens, and an alert nobody opens is
not an alert. Silence means healthy.

Configured with a Gmail app password rather than a transactional email service:
one secret, no account to sign up for, and the volume is a handful of messages a
year. If it is not configured the check still runs and logs — a missing password
must not take the scheduler down with it.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)

_HOST = "smtp.gmail.com"
_PORT = 465


def configured() -> bool:
    cfg = settings()
    return bool(cfg.alert_email_to and cfg.alert_smtp_user and cfg.alert_smtp_password)


def send(subject: str, body: str) -> bool:
    """Send one alert. Returns whether it went out.

    Never raises. This is called from a scheduled job, and an alert failing to
    send must not stop the job that noticed the problem.
    """
    cfg = settings()
    if not configured():
        log.warning("alert not sent, no email configured: %s", subject)
        return False

    message = EmailMessage()
    message["Subject"] = f"[Coach Will] {subject}"
    message["From"] = cfg.alert_smtp_user
    message["To"] = cfg.alert_email_to
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(_HOST, _PORT, timeout=20) as smtp:
            smtp.login(cfg.alert_smtp_user, cfg.alert_smtp_password)
            smtp.send_message(message)
        log.info("alert sent: %s", subject)
        return True
    except Exception:
        log.exception("alert failed to send: %s", subject)
        return False
