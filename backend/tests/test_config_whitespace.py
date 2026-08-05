"""Secrets must survive stray whitespace.

A single leading space on ANTHROPIC_API_KEY made httpx raise LocalProtocolError
before any request left the process, which the Anthropic SDK reported as
APIConnectionError. The daily brief and chat both failed, and both looked like
network outages rather than a malformed secret.

Whitespace reaches secrets easily: a trailing newline from a pipe, a space after
`=` in an .env file, an editor helpfully reformatting a paste. Stripping is
cheap; debugging it again is not.
"""

import pytest

from app.config import Settings

BASE = {
    "database_url": "postgresql+psycopg://u:p@localhost/db",
    "jwt_secret": "s" * 32,
    "fernet_key": "k" * 44,
}


def test_api_key_leading_space_is_stripped():
    s = Settings(**BASE, anthropic_api_key="  sk-ant-test-key")
    assert s.anthropic_api_key == "sk-ant-test-key"


def test_api_key_trailing_newline_is_stripped():
    s = Settings(**BASE, anthropic_api_key="sk-ant-test-key\n")
    assert s.anthropic_api_key == "sk-ant-test-key"


@pytest.mark.parametrize(
    "field",
    ["oura_client_secret", "strava_client_secret", "oura_client_id", "strava_client_id"],
)
def test_provider_credentials_are_stripped(field):
    s = Settings(**BASE, **{field: "  value  "})
    assert getattr(s, field) == "value"


def test_stripping_runs_before_the_database_url_rewrite():
    """Order matters: the scheme rewrite does a prefix match, so whitespace has
    to be gone before it runs or `postgres://` is not recognised."""
    s = Settings(
        **{**BASE, "database_url": "  postgres://u:p@localhost/db  "}
    )
    assert s.database_url == "postgresql+psycopg://u:p@localhost/db"


def test_non_string_settings_are_untouched():
    s = Settings(**BASE, chat_daily_message_limit=10)
    assert s.chat_daily_message_limit == 10
