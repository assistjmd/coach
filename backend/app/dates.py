"""Calendar days, in the athlete's timezone rather than the server's.

"Today" is not a server-side fact. The API runs in UTC, so `date.today()` there
rolls over at 7pm in Boston and 4pm in California — an evening workout gets filed
under tomorrow, and the dashboard starts asking for a brief that will not exist
until the next morning.

Anywhere a `datetime` becomes a calendar day, it has to pass through here with
the user's zone.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


def zone_for(user) -> ZoneInfo:
    """The user's timezone, falling back to UTC on anything unusable.

    A stale or malformed identifier must degrade rather than raise: it is stored
    from the device and we do not control what arrives.
    """
    try:
        return ZoneInfo(getattr(user, "timezone", None) or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r for user %s",
                    getattr(user, "timezone", None), getattr(user, "id", "?"))
        return ZoneInfo("UTC")


def today_for(user) -> date:
    """The date it currently is where the user is."""
    return datetime.now(tz=zone_for(user)).date()


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    """The calendar day a timestamp falls on, locally.

    Naive datetimes are assumed UTC, which is what the database stores. Treating
    them as local instead would shift every historical row.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date()
