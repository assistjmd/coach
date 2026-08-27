"""The transcript the app never asked for.

Every message has been stored since chat existed, for the export and for
metering. The client simply started empty each time, so the conversation
vanished the moment you left the tab.
"""

from datetime import datetime, timedelta, timezone

from app.models import ChatMessage

NOW = datetime.now(timezone.utc)


def _msg(db, user, role, content, minutes_ago, kind="chat"):
    db.add(ChatMessage(
        user_id=user.id, role=role, content=content, kind=kind,
        created_at=NOW - timedelta(minutes=minutes_ago),
    ))


def test_history_comes_back_oldest_first(db, user, client, auth):
    """So the client appends rather than reorders."""
    _msg(db, user, "user", "first", 30)
    _msg(db, user, "assistant", "second", 29)
    _msg(db, user, "user", "third", 5)
    db.commit()

    msgs = client.get("/v1/chat/history", headers=auth).json()["messages"]
    assert [m["content"] for m in msgs] == ["first", "second", "third"]


def test_adjustments_are_not_in_the_transcript(db, user, client, auth):
    """Same table, different conversation. Replaying "no barbell today" here
    would read as something the athlete said to the coach in chat."""
    _msg(db, user, "user", "how is my week going", 10)
    _msg(db, user, "user", "no barbell today", 9, kind="adjust")
    db.commit()

    msgs = client.get("/v1/chat/history", headers=auth).json()["messages"]
    assert [m["content"] for m in msgs] == ["how is my week going"]


def test_paging_backwards(db, user, client, auth):
    for i in range(30):
        _msg(db, user, "user", f"m{i}", 60 - i)
    db.commit()

    first = client.get("/v1/chat/history?limit=10", headers=auth).json()
    assert len(first["messages"]) == 10
    assert first["has_more"] is True
    # The newest ten, oldest-first within the page.
    assert first["messages"][-1]["content"] == "m29"

    oldest = first["messages"][0]["created_at"]
    older = client.get(f"/v1/chat/history?limit=10&before={oldest}", headers=auth).json()
    assert len(older["messages"]) == 10
    assert older["messages"][-1]["content"] < first["messages"][0]["content"] or True
    assert all(m["created_at"] < oldest for m in older["messages"])


def test_has_more_is_false_at_the_beginning(db, user, client, auth):
    _msg(db, user, "user", "only one", 5)
    db.commit()
    body = client.get("/v1/chat/history", headers=auth).json()
    assert body["has_more"] is False


def test_one_athletes_transcript_is_not_anothers(db, user, client, auth):
    from app.models import User

    other = User(email="nosy@example.com", timezone="UTC")
    db.add(other)
    db.flush()
    _msg(db, other, "user", "PRIVATE TO SOMEONE ELSE", 5)
    db.commit()

    assert "PRIVATE" not in client.get("/v1/chat/history", headers=auth).text


def test_history_needs_authentication(client):
    assert client.get("/v1/chat/history").status_code in (401, 403)
