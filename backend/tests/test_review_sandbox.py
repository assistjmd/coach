"""App Review has to be able to complete a purchase.

A reviewer installs the build, signs in — creating an account id nobody could
have listed in advance — and buys with a sandbox Apple ID, because review
always transacts in Sandbox. Refusing that reads to Apple as an in-app purchase
that does not work.
"""

import uuid

from app.config import settings
from app.entitlement import allowed_environments


def _env(monkeypatch, listed: str):
    settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SANDBOX_SUBSCRIPTION_USER_IDS", listed)


def test_an_unknown_account_is_production_only_by_default(monkeypatch):
    _env(monkeypatch, "")
    assert allowed_environments(uuid.uuid4()) == ("Production",)
    settings.cache_clear()


def test_the_wildcard_lets_a_reviewer_buy(monkeypatch):
    _env(monkeypatch, "*")
    assert "Sandbox" in allowed_environments(uuid.uuid4())
    settings.cache_clear()


def test_a_listed_account_works_without_the_wildcard(monkeypatch):
    known = uuid.uuid4()
    _env(monkeypatch, str(known))
    assert "Sandbox" in allowed_environments(known)
    assert allowed_environments(uuid.uuid4()) == ("Production",)
    settings.cache_clear()
