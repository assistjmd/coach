"""What a dead connection tells the athlete.

Google revokes refresh tokens after seven days while an OAuth app's publishing
status is Testing. That is not a transient failure and no retry fixes it, so the
only useful response is to say "reconnect" — which the athlete never saw,
because the token endpoint's response body was not logged and the error reached
the Sources row as `HTTPStatusError: Client error '400 Bad Request'`.
"""

import httpx
import pytest

from app.providers.base import ReauthRequired
from app.providers.google_health import _check_token_response


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x"))


def test_invalid_grant_asks_the_athlete_to_reconnect():
    with pytest.raises(ReauthRequired) as e:
        _check_token_response(_response(400, {"error": "invalid_grant"}))
    message = str(e.value)
    assert "reconnect" in message.lower()
    # Written for a person, not a log: no status codes, no exception names.
    assert "400" not in message
    assert "invalid_grant" not in message


def test_unauthorized_client_is_also_a_reconnect():
    with pytest.raises(ReauthRequired):
        _check_token_response(_response(401, {"error": "unauthorized_client"}))


def test_other_failures_stay_http_errors():
    """A genuine server fault must not be mislabelled as the user's problem —
    reconnecting would not fix it and would waste their time."""
    with pytest.raises(httpx.HTTPStatusError):
        _check_token_response(_response(500, {"error": "backend_error"}))


def test_a_good_response_passes_through():
    _check_token_response(_response(200, {"access_token": "t"}))


def test_a_non_json_error_body_does_not_crash():
    r = httpx.Response(502, text="<html>bad gateway</html>",
                       request=httpx.Request("POST", "https://x"))
    with pytest.raises(httpx.HTTPStatusError):
        _check_token_response(r)
