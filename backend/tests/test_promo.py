"""Promo codes extend the trial. They never grant a subscription.

A subscription is Apple's to grant and ours to verify. Writing one here would
put a row in the table that no Apple signature backs, and every count of paying
customers would be wrong from then on.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import PromoCode, PromoRedemption, Subscription

NOW = datetime.now(timezone.utc)


@pytest.fixture
def code(db):
    c = PromoCode(code="TRAINON", extra_days=30, note="beta testers")
    db.add(c)
    db.commit()
    return c


def test_a_valid_code_extends_the_trial(client, auth, db, user, code):
    user.trial_ends_at = NOW + timedelta(days=2)
    db.commit()

    r = client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"})
    assert r.status_code == 200
    assert r.json()["extra_days"] == 30

    db.refresh(user)
    # Thirty days on top of what they had, not thirty from today.
    assert (user.trial_ends_at - NOW).days >= 31


def test_a_lapsed_trial_extends_from_now(client, auth, db, user, code):
    """A month from a date already gone is not a month."""
    user.trial_ends_at = NOW - timedelta(days=10)
    db.commit()

    client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"})
    db.refresh(user)
    assert 29 <= (user.trial_ends_at - NOW).days <= 30


def test_case_and_spacing_do_not_matter(client, auth, code):
    """Someone typing this on a phone will get the case wrong, and telling them
    their correct code is wrong is a support email you did not need."""
    r = client.post("/v1/promo/redeem", headers=auth, json={"code": "  trainon  "})
    assert r.status_code == 200


def test_a_code_cannot_be_used_twice(client, auth, db, user, code):
    assert client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"}).status_code == 200
    second = client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"})
    assert second.status_code == 409
    assert "already used" in second.json()["detail"]
    assert db.query(PromoRedemption).filter(PromoRedemption.user_id == user.id).count() == 1


def test_an_unknown_code_is_refused(client, auth):
    assert client.post("/v1/promo/redeem", headers=auth, json={"code": "NOPE"}).status_code == 404


def test_expired_and_unknown_read_identically(client, auth, db):
    """Distinguishing them tells someone probing which guesses were close."""
    db.add(PromoCode(code="GONE", extra_days=30, expires_on=date.today() - timedelta(days=1)))
    db.commit()

    expired = client.post("/v1/promo/redeem", headers=auth, json={"code": "GONE"})
    unknown = client.post("/v1/promo/redeem", headers=auth, json={"code": "NEVEREXISTED"})
    assert expired.status_code == unknown.status_code == 404
    assert expired.json()["detail"] == unknown.json()["detail"]


def test_a_deactivated_code_stops_working(client, auth, db, code):
    code.active = False
    db.commit()
    assert client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"}).status_code == 404


def test_a_cap_stops_a_code_becoming_a_free_tier(client, auth, db, user):
    """What stops a code posted publicly from being permanent."""
    from app.models import User

    capped = PromoCode(code="LIMITED", extra_days=30, max_redemptions=1)
    db.add(capped)
    other = User(email="first@example.com", timezone="UTC")
    db.add(other)
    db.flush()
    db.add(PromoRedemption(
        user_id=other.id, promo_code_id=capped.id, extended_to=NOW + timedelta(days=30)
    ))
    db.commit()

    r = client.post("/v1/promo/redeem", headers=auth, json={"code": "LIMITED"})
    assert r.status_code == 409
    assert "fully claimed" in r.json()["detail"]


def test_redeeming_never_creates_a_subscription(client, auth, db, user, code):
    client.post("/v1/promo/redeem", headers=auth, json={"code": "TRAINON"})
    assert db.query(Subscription).filter(Subscription.user_id == user.id).count() == 0
    body = client.get("/v1/subscriptions/status", headers=auth).json()
    assert body["state"] == "trial", "a promo is a longer trial, not a purchase"


def test_it_needs_authentication(client):
    assert client.post("/v1/promo/redeem", json={"code": "TRAINON"}).status_code in (401, 403)
