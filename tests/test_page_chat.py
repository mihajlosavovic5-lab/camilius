"""Tests for POST /p/<slug>/chat - F3, chat + booking on the hosted public page.
Exercises the real route (not ai_replies.handle_incoming_message directly) so the
session-key plumbing, the auto-send bypass, and the cost-protection gates are all
proven end-to-end, the same reasoning test_webhook_processing.py gives for the three
Meta webhooks this route now shares handle_incoming_message with."""
import uuid

import database
import ai_replies
from conftest import register_business_web


def _publish_page(client, username, email):
    register_business_web(client, username, email)
    business_id = database.business_by_username(username)[0]
    slug = f"page-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "Family-run since 2019.")
    return business_id, slug


def _session_id():
    return f"web:{uuid.uuid4()}"


def _stub_ai(monkeypatch, replies):
    """Same technique as test_webhook_processing.py's _stub_ai - patches the two
    ai_replies-imported entry points so no real OpenAI call happens."""
    calls = []

    def fake_generate_reply_with_booking(text, business_info, history, now_local_display, tool_impls, channel=None):
        calls.append({"text": text, "history": history, "channel": channel, "tool_impls": tool_impls})
        return replies[len(calls) - 1]

    def fake_generate_reply(text, business_info, history=None, channel=None):
        calls.append({"text": text, "history": history, "channel": channel})
        return replies[len(calls) - 1]

    monkeypatch.setattr(ai_replies, "generate_reply_with_booking", fake_generate_reply_with_booking)
    monkeypatch.setattr(ai_replies, "generate_reply", fake_generate_reply)
    return calls


def test_page_chat_returns_reply_and_saves_sent_conversation(client, unique_username, unique_email, monkeypatch):
    business_id, slug = _publish_page(client, unique_username, unique_email)
    calls = _stub_ai(monkeypatch, ["We're open 9-6, come by any time!"])
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)
    session_id = _session_id()

    response = client.post(f"/p/{slug}/chat", json={"message": "Are you open today?", "session_id": session_id})
    assert response.status_code == 200
    assert response.get_json()["reply"] == "We're open 9-6, come by any time!"
    assert calls[0]["channel"] == "web"

    conversations = database.conversations_for_business(business_id)
    assert len(conversations) == 1
    # status column - web chat forces auto-send, so this must never sit as a pending
    # draft the way a Meta channel with auto_send off would (see ai_replies.py).
    assert conversations[0][6] == "sent"
    assert conversations[0][10] == "web"  # channel column


def test_page_chat_404s_for_unknown_slug(client):
    response = client.post(f"/p/does-not-exist-{uuid.uuid4().hex[:8]}/chat",
                            json={"message": "hi", "session_id": _session_id()})
    assert response.status_code == 404


def test_page_chat_404s_when_not_published(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)

    response = client.post(f"/p/{slug}/chat", json={"message": "hi", "session_id": _session_id()})
    assert response.status_code == 404


def test_page_chat_rejects_missing_message_or_session_id(client, unique_username, unique_email):
    _, slug = _publish_page(client, unique_username, unique_email)

    assert client.post(f"/p/{slug}/chat", json={"session_id": _session_id()}).status_code == 400
    assert client.post(f"/p/{slug}/chat", json={"message": "hi"}).status_code == 400
    assert client.post(f"/p/{slug}/chat", json={"message": "  ", "session_id": _session_id()}).status_code == 400


def test_page_chat_remembers_history_within_same_session(client, unique_username, unique_email, monkeypatch):
    _, slug = _publish_page(client, unique_username, unique_email)
    calls = _stub_ai(monkeypatch, ["We're open 9-6.", "Yes, Saturdays too."])
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)
    session_id = _session_id()

    client.post(f"/p/{slug}/chat", json={"message": "Are you open?", "session_id": session_id})
    client.post(f"/p/{slug}/chat", json={"message": "What about Saturdays?", "session_id": session_id})

    assert len(calls) == 2
    assert calls[0]["history"] == []
    assert calls[1]["history"] == [("Are you open?", "We're open 9-6.")]


def test_page_chat_does_not_leak_history_across_different_sessions(client, unique_username, unique_email, monkeypatch):
    _, slug = _publish_page(client, unique_username, unique_email)
    calls = _stub_ai(monkeypatch, ["We're open 9-6.", "Sure, haircuts are 20 EUR."])
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    client.post(f"/p/{slug}/chat", json={"message": "Are you open?", "session_id": _session_id()})
    client.post(f"/p/{slug}/chat", json={"message": "How much for a haircut?", "session_id": _session_id()})

    assert calls[1]["history"] == []  # a different visitor - must start with no memory of the first one


def test_page_chat_enforces_per_session_daily_cap(client, unique_username, unique_email, monkeypatch):
    _, slug = _publish_page(client, unique_username, unique_email)
    monkeypatch.setattr("app.PAGE_CHAT_SESSION_DAILY_LIMIT", 1)
    calls = _stub_ai(monkeypatch, ["First reply.", "should not be reached"])
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)
    session_id = _session_id()

    first = client.post(f"/p/{slug}/chat", json={"message": "Hi", "session_id": session_id})
    assert first.get_json()["reply"] == "First reply."

    second = client.post(f"/p/{slug}/chat", json={"message": "Hi again", "session_id": session_id})
    assert second.status_code == 200
    assert "answered a lot of questions" in second.get_json()["reply"]
    assert len(calls) == 1  # the capped message never reached the AI - the actual cost protection


def test_page_chat_books_an_appointment_with_session_id_as_sender_key(client, unique_username, unique_email, monkeypatch, future_date):
    business_id, slug = _publish_page(client, unique_username, unique_email)
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)
    session_id = _session_id()

    def fake_generate_reply_with_booking(text, business_info, history, now_local_display, tool_impls, channel=None):
        result = tool_impls["check_and_book_appointment"](f"{future_date}T10:00:00")
        return f"Booked: {result['status']}"

    monkeypatch.setattr(ai_replies, "generate_reply_with_booking", fake_generate_reply_with_booking)

    response = client.post(f"/p/{slug}/chat", json={"message": "Book me a haircut tomorrow at 10", "session_id": session_id})
    assert response.status_code == 200
    assert "Booked: booked" in response.get_json()["reply"]

    appointments = database.all_appointments_for_business(business_id)
    assert len(appointments) == 1
    assert appointments[0][0] == f"Web visitor #{session_id[-6:]}"


def test_send_channel_reply_is_a_noop_for_web_channel(client, unique_username, unique_email):
    """A page-chat conversation always reaches sent status the moment it's created (see
    ai_replies.handle_incoming_message) - a manual /send should never actually need to
    deliver anything, so send_channel_reply is unconditionally a no-op."""
    import app as app_module

    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    business = database.business_by_id(business_id)

    # get_conversation()'s own row shape: id, business_id, sender, subject, question,
    # suggested_reply, sender_igsid, channel, sender_wa_id, sender_psid.
    conversation = (1, business_id, "Web visitor #abc123", None, "hi", "hello", "web-session-key", "web", None, None)
    result = app_module.send_channel_reply(conversation, business, "hello")
    assert result is None
