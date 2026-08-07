"""Data export.

The privacy policy promises a copy of everything. Two ways that goes wrong: it
quietly omits a table, or it includes live OAuth credentials in a file that ends
up in someone's Downloads folder.
"""

from datetime import date, datetime, timezone

from app.models import DailyBrief, OAuthToken, User, Workout


def test_export_is_a_named_download(client, auth):
    resp = client.get("/v1/me/export", headers=auth)
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "coach-will-export-" in resp.headers["content-disposition"]


def test_every_section_is_present_even_when_empty(client, auth):
    """A missing key and an empty list read very differently to whoever receives
    this file. Absent sections look like withheld data."""
    body = client.get("/v1/me/export", headers=auth).json()
    for section in (
        "account", "connections", "consents", "goals", "briefs", "day_logs",
        "workouts", "sleep", "hrv", "heart_rate", "readiness", "body_metrics",
        "chat",
    ):
        assert section in body, f"export omits {section}"


def test_credentials_are_never_exported(client, auth, db, user):
    """The connection is disclosed. The keys to it are not."""
    db.add(
        OAuthToken(
            user_id=user.id,
            provider="strava",
            access_token_enc="SECRET-ACCESS",
            refresh_token_enc="SECRET-REFRESH",
            scope="read,activity:read_all",
        )
    )
    db.commit()

    raw = client.get("/v1/me/export", headers=auth).text
    assert "SECRET-ACCESS" not in raw
    assert "SECRET-REFRESH" not in raw

    body = client.get("/v1/me/export", headers=auth).json()
    conn = body["connections"][0]
    assert conn["provider"] == "strava"
    assert conn["scope"] == "read,activity:read_all"
    assert "access_token" not in conn


def test_content_is_actually_included(client, auth, db, user):
    db.add(
        Workout(
            user_id=user.id,
            source="strava",
            source_record_id="w-1",
            started_at=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
            duration_s=3600,
            sport="running",
            avg_hr=150,
        )
    )
    db.add(
        DailyBrief(
            user_id=user.id,
            day=date(2026, 8, 6),
            readiness_summary="Recovered.",
            training_prescription="45 min easy.",
            model="claude-sonnet-4-6",
        )
    )
    db.commit()

    body = client.get("/v1/me/export", headers=auth).json()
    assert body["workouts"][0]["sport"] == "running"
    assert body["workouts"][0]["avg_hr"] == 150
    # Datetimes must be strings, not something that fails to serialize.
    assert isinstance(body["workouts"][0]["started_at"], str)
    assert body["briefs"][0]["training_prescription"] == "45 min easy."


def test_one_account_cannot_export_another(client, auth, db):
    other = User(email="someone-else@example.com", timezone="UTC")
    db.add(other)
    db.flush()
    db.add(
        Workout(
            user_id=other.id,
            source="strava",
            source_record_id="not-yours",
            started_at=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
            ended_at=datetime(2026, 8, 6, 13, tzinfo=timezone.utc),
            duration_s=3600,
        )
    )
    db.commit()

    raw = client.get("/v1/me/export", headers=auth).text
    assert "not-yours" not in raw


def test_export_requires_authentication(client):
    assert client.get("/v1/me/export").status_code in (401, 403)
