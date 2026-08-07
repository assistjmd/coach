"""Account deletion and legal consent.

Deletion is an App Store requirement (5.1.1(v)) and a GDPR/CCPA one, and it is
the kind of endpoint that quietly half-works: the user row goes and the health
data stays, which is worse than not offering it.

Consent matters for the opposite reason — it only has value if the record is
accurate about *which version* was accepted.
"""

from datetime import datetime, timezone

from app.models import (
    BodyMetric,
    ChatMessage,
    DailyBrief,
    Goal,
    HRVReading,
    LegalConsent,
    OAuthToken,
    User,
    Workout,
)
from app.routes.me import CURRENT_LEGAL_VERSION

NOW = datetime(2026, 8, 1, 6, tzinfo=timezone.utc)


def _populate(db, user: User) -> None:
    """Give the account something in every table that hangs off it."""
    db.add_all(
        [
            Goal(user_id=user.id, kind="adaptive"),
            DailyBrief(
                user_id=user.id,
                day=NOW.date(),
                readiness_summary="s",
                training_prescription="p",
                model="test",
            ),
            ChatMessage(user_id=user.id, role="user", content="hello"),
            Workout(
                user_id=user.id,
                source="strava",
                source_record_id="w1",
                sport="running",
                started_at=NOW,
                ended_at=NOW,
                duration_s=600,
            ),
            HRVReading(user_id=user.id, source="oura", measured_at=NOW, rmssd_ms=60),
            BodyMetric(
                user_id=user.id, source="healthkit", metric="steps",
                value=8000, measured_at=NOW,
            ),
            OAuthToken(user_id=user.id, provider="oura", access_token_enc="x"),
            LegalConsent(
                user_id=user.id, document="terms", version=CURRENT_LEGAL_VERSION
            ),
        ]
    )
    db.commit()


def test_delete_removes_every_trace(client, db, user, auth):
    _populate(db, user)

    resp = client.delete("/v1/me", headers=auth)
    assert resp.status_code == 204

    db.expire_all()
    assert db.get(User, user.id) is None
    # The point of the test: cascade actually reaches the health data. A user row
    # disappearing while HRV and workouts remain is not an erasure.
    for model in (
        Goal, DailyBrief, ChatMessage, Workout, HRVReading, BodyMetric,
        OAuthToken, LegalConsent,
    ):
        remaining = db.query(model).filter(model.user_id == user.id).count()
        assert remaining == 0, f"{model.__name__} survived deletion"


def test_deleted_account_cannot_keep_using_its_token(client, user, auth):
    assert client.delete("/v1/me", headers=auth).status_code == 204
    # The JWT is still cryptographically valid; the user behind it is gone.
    assert client.get("/v1/me", headers=auth).status_code in (401, 404)


def test_consent_is_recorded_for_both_documents(client, db, user, auth):
    resp = client.post(
        "/v1/me/consent",
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": "1990-05-14"},
        headers=auth,
    )
    assert resp.status_code == 201
    assert resp.json()["up_to_date"] is True

    docs = {
        c.document
        for c in db.query(LegalConsent).filter(LegalConsent.user_id == user.id)
    }
    assert docs == {"terms", "privacy"}


def test_stale_version_is_refused(client, auth):
    """A stale client must not be able to write a record that reads as agreement
    to documents the user never saw."""
    resp = client.post(
        "/v1/me/consent", json={"version": "1999-01-01"}, headers=auth
    )
    assert resp.status_code == 409


def test_status_reports_not_accepted_before_consent(client, auth):
    body = client.get("/v1/me/consent", headers=auth).json()
    assert body["accepted_version"] is None
    assert body["up_to_date"] is False
    assert body["current_version"] == CURRENT_LEGAL_VERSION


def test_consent_history_is_append_only(client, db, user, auth):
    """Re-accepting must not overwrite the earlier record — the question later is
    which versions were accepted and when."""
    client.post(
        "/v1/me/consent",
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": "1990-05-14"},
        headers=auth,
    )
    client.post(
        "/v1/me/consent",
        json={"version": CURRENT_LEGAL_VERSION, "date_of_birth": "1990-05-14"},
        headers=auth,
    )
    count = db.query(LegalConsent).filter(LegalConsent.user_id == user.id).count()
    assert count == 4  # two documents, twice
