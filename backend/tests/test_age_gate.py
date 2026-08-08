"""The 16+ gate, and the export it feeds.

The terms have always said 16 and over. Nothing asked, which made the clause
decorative. The gate lives on the consent endpoint because agreeing to terms you
are too young to agree to is precisely the thing being prevented.
"""

from datetime import date, timedelta

import pytest

from app.dates import today_for
from app.models import LegalConsent, User
from app.routes.me import CURRENT_LEGAL_VERSION, MINIMUM_AGE, _age_on


def _dob_years_ago(user, years: int, days: int = 0) -> str:
    """Relative to the *user's* today, not the machine's.

    The server resolves "today" in the account's timezone. A test that builds
    its dates from the runner's local clock disagrees with it for part of every
    day — which is the same bug this whole area exists to fix.
    """
    return (today_for(user) - timedelta(days=years * 365.25 + days)).isoformat()


def test_age_arithmetic_handles_an_unreached_birthday():
    """Born late in the year, checked early in the next: still 15."""
    assert _age_on(date(2010, 12, 1), date(2026, 11, 30)) == 15
    assert _age_on(date(2010, 12, 1), date(2026, 12, 1)) == 16
    assert _age_on(date(2010, 12, 1), date(2026, 12, 2)) == 16


def test_adult_is_accepted_and_the_date_is_stored(client, auth, db, user):
    resp = client.post(
        "/v1/me/consent",
        headers=auth,
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": "1990-05-14"},
    )
    assert resp.status_code == 201
    assert resp.json()["up_to_date"] is True

    db.refresh(user)
    assert user.date_of_birth == date(1990, 5, 14)


def test_under_sixteen_is_refused_and_no_consent_is_recorded(client, auth, db, user):
    resp = client.post(
        "/v1/me/consent",
        headers=auth,
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": _dob_years_ago(user, 12)},
    )
    assert resp.status_code == 403
    assert str(MINIMUM_AGE) in resp.json()["detail"]

    # The important half: a refused user must not end up holding a consent
    # record, or everything downstream treats them as having agreed.
    assert not db.query(LegalConsent).filter(LegalConsent.user_id == user.id).count()
    db.refresh(user)
    assert user.date_of_birth is None


def test_exactly_sixteen_today_is_allowed(client, auth, user):
    """Boundary in the user's favour — 16 means 16, not 16 and a day."""
    today = today_for(user)
    sixteenth = today.replace(year=today.year - MINIMUM_AGE)
    resp = client.post(
        "/v1/me/consent",
        headers=auth,
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": sixteenth.isoformat()},
    )
    assert resp.status_code == 201


def test_missing_date_of_birth_is_rejected(client, auth):
    """Older clients must not be able to consent without answering."""
    resp = client.post(
        "/v1/me/consent", headers=auth, json={"version": CURRENT_LEGAL_VERSION}
    )
    assert resp.status_code == 400


def test_future_date_of_birth_is_rejected(client, auth, user):
    resp = client.post(
        "/v1/me/consent",
        headers=auth,
        json={
            "version": CURRENT_LEGAL_VERSION,
            # Tomorrow in the account's timezone. Built from the runner's clock
            # this lands on today for part of every day, and the request is then
            # refused as under-16 instead of as future-dated.
            "date_of_birth": (today_for(user) + timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 400


def test_reconsent_does_not_require_resending_the_date(client, auth, db, user):
    """Once stored, a version bump must not re-ask for something we already have."""
    user.date_of_birth = date(1990, 5, 14)
    db.commit()

    resp = client.post(
        "/v1/me/consent", headers=auth, json={"version": CURRENT_LEGAL_VERSION}
    )
    assert resp.status_code == 201
