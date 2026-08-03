"""End-to-end tests for the three Meta webhook routes, through the real
/instagram-webhook, /whatsapp-webhook and /messenger-webhook POST handlers - not just the
signature/parsing helpers test_instagram_helpers.py etc. already cover.

These exist specifically to de-risk ai_replies.handle_incoming_message: the shared
pipeline that _process_instagram_message/_process_whatsapp_message/_process_messenger_message
now all delegate to, replacing ~110 near-identical lines each. Two things matter most here:

1. Conversation memory actually works end-to-end. app.py used to pass sender_igsid to
   previous_conversations_with(), which filters on conversations.sender - populated with
   sender_display, not the igsid - so Instagram conversations silently had no memory
   whenever the profile-name lookup succeeded. That's fixed, but only a real second
   webhook call that checks what history generate_reply_with_booking actually received
   proves it stays fixed.

2. Cost ordering is preserved: Instagram's sender resolution is a real Graph API call
   (get_sender_info), and the original code only ever paid for it AFTER the
   subscription/daily-limit/dedup gates passed. ai_replies.handle_incoming_message takes
   that resolution as a deferred callback for exactly this reason - a skipped message
   must never trigger it.
"""
import uuid

import ai_replies
import app as app_module
import database
from conftest import register_and_login


def _signed_post(client, path, payload, secret):
    import hashlib
    import hmac
    import json

    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(path, data=body, content_type="application/json",
                        headers={"X-Hub-Signature-256": signature})


def _instagram_payload(ig_account_id, sender_igsid, text, mid=None):
    return {"entry": [{"id": ig_account_id, "messaging": [{
        "sender": {"id": sender_igsid},
        "message": {"mid": mid or f"mid-{uuid.uuid4().hex[:8]}", "text": text},
    }]}]}


def _whatsapp_payload(phone_number_id, sender_wa_id, text, sender_name=None, mid=None):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "contacts": [{"wa_id": sender_wa_id, "profile": {"name": sender_name}}] if sender_name else [],
        "messages": [{
            "id": mid or f"mid-{uuid.uuid4().hex[:8]}", "from": sender_wa_id,
            "type": "text", "text": {"body": text},
        }],
    }}]}]}


def _messenger_payload(page_id, sender_psid, text, mid=None):
    return {"entry": [{"id": page_id, "messaging": [{
        "sender": {"id": sender_psid},
        "message": {"mid": mid or f"mid-{uuid.uuid4().hex[:8]}", "text": text},
    }]}]}


def _stub_ai(monkeypatch, replies):
    """Replaces both AI entry points ai_replies actually calls with a stub that returns
    replies in order and records the history it was passed, so tests can assert on
    conversation memory without a real OpenAI call. Patched on ai_replies (where the
    names are imported TO), not generate_reply (where they're imported FROM)."""
    calls = []

    def fake_generate_reply_with_booking(text, business_info, history, now_local_display, tool_impls, channel=None):
        calls.append({"text": text, "history": history, "channel": channel})
        return replies[len(calls) - 1]

    def fake_generate_reply(text, business_info, history=None, channel=None):
        calls.append({"text": text, "history": history, "channel": channel})
        return replies[len(calls) - 1]

    monkeypatch.setattr(ai_replies, "generate_reply_with_booking", fake_generate_reply_with_booking)
    monkeypatch.setattr(ai_replies, "generate_reply", fake_generate_reply)
    return calls


def test_instagram_webhook_saves_conversation_and_auto_sends(client, unique_username, unique_email, monkeypatch):
    _, business_id = register_and_login(client, unique_username, unique_email)
    ig_account_id = f"ig-{uuid.uuid4().hex[:10]}"
    database.link_instagram_account(business_id, ig_account_id, "the_biz", "fake-token")

    calls = _stub_ai(monkeypatch, ["Sure, we're open 9-6!"])
    monkeypatch.setattr(app_module, "get_sender_info", lambda igsid, token: {
        "display_name": "jane_doe", "profile_pic_url": "https://example.com/pic.jpg",
    })
    sent = []
    monkeypatch.setattr(app_module, "send_instagram_message",
                         lambda igsid, text, token, account_id: sent.append((igsid, text)))
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    response = _signed_post(client, "/instagram-webhook",
                             _instagram_payload(ig_account_id, "igsid-1", "Are you open?"),
                             "test-instagram-app-secret")
    assert response.status_code == 200
    assert calls[0]["channel"] == "instagram"
    assert calls[0]["history"] == []  # first message from this sender - nothing yet
    assert sent == [("igsid-1", "Sure, we're open 9-6!")]

    conversations = database.conversations_for_business(business_id)
    assert len(conversations) == 1
    assert conversations[0][1] == "jane_doe"  # sender column holds the resolved display name


def test_instagram_webhook_conversation_has_memory_on_second_message(client, unique_username, unique_email, monkeypatch):
    """Regression test for the sender/sender_igsid mismatch: history must contain the
    first exchange by the time the second message from the same customer arrives."""
    _, business_id = register_and_login(client, unique_username, unique_email)
    ig_account_id = f"ig-{uuid.uuid4().hex[:10]}"
    database.link_instagram_account(business_id, ig_account_id, "the_biz", "fake-token")

    calls = _stub_ai(monkeypatch, ["We're open 9-6.", "Yes, Saturdays too."])
    monkeypatch.setattr(app_module, "get_sender_info", lambda igsid, token: {
        "display_name": "jane_doe", "profile_pic_url": None,
    })
    monkeypatch.setattr(app_module, "send_instagram_message", lambda *a, **k: None)
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    _signed_post(client, "/instagram-webhook",
                 _instagram_payload(ig_account_id, "igsid-1", "Are you open?"),
                 "test-instagram-app-secret")
    _signed_post(client, "/instagram-webhook",
                 _instagram_payload(ig_account_id, "igsid-1", "What about Saturdays?"),
                 "test-instagram-app-secret")

    assert len(calls) == 2
    assert calls[0]["history"] == []
    assert calls[1]["history"] == [("Are you open?", "We're open 9-6.")]


def test_instagram_webhook_skips_sender_resolution_when_daily_limit_reached(client, unique_username, unique_email, monkeypatch):
    """A skipped message must never pay for get_sender_info's real Graph API call - see
    module docstring point 2."""
    _, business_id = register_and_login(client, unique_username, unique_email)
    ig_account_id = f"ig-{uuid.uuid4().hex[:10]}"
    database.link_instagram_account(business_id, ig_account_id, "the_biz", "fake-token")

    monkeypatch.setattr(ai_replies, "replies_today_count", lambda bid: ai_replies.DAILY_REPLY_LIMIT)
    resolve_calls = []
    monkeypatch.setattr(app_module, "get_sender_info", lambda igsid, token: resolve_calls.append(igsid) or {
        "display_name": "jane_doe", "profile_pic_url": None,
    })
    calls = _stub_ai(monkeypatch, ["should not be reached"])

    response = _signed_post(client, "/instagram-webhook",
                             _instagram_payload(ig_account_id, "igsid-1", "Are you open?"),
                             "test-instagram-app-secret")
    assert response.status_code == 200
    assert resolve_calls == []
    assert calls == []
    assert database.conversations_for_business(business_id) == []


def test_whatsapp_webhook_saves_conversation(client, unique_username, unique_email, monkeypatch):
    _, business_id = register_and_login(client, unique_username, unique_email)
    phone_number_id = f"wa-{uuid.uuid4().hex[:10]}"
    database.link_whatsapp_account(business_id, phone_number_id, "+1 555 0100", "fake-token")

    calls = _stub_ai(monkeypatch, ["Yes, come on by!"])
    sent = []
    monkeypatch.setattr(app_module.whatsapp_api, "send_message",
                         lambda wa_id, text, token, display: sent.append((wa_id, text)))
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    response = _signed_post(client, "/whatsapp-webhook",
                             _whatsapp_payload(phone_number_id, "38160123456", "Any slots today?", sender_name="Marko"),
                             "test-whatsapp-app-secret")
    assert response.status_code == 200
    assert calls[0]["channel"] == "whatsapp"
    assert sent == [("38160123456", "Yes, come on by!")]

    conversations = database.conversations_for_business(business_id)
    assert len(conversations) == 1
    assert conversations[0][1] == "Marko"


def test_messenger_webhook_saves_conversation(client, unique_username, unique_email, monkeypatch):
    _, business_id = register_and_login(client, unique_username, unique_email)
    page_id = f"pg-{uuid.uuid4().hex[:10]}"
    database.link_messenger_page(business_id, page_id, "The Business Page", "fake-token")

    calls = _stub_ai(monkeypatch, ["Sure thing!"])
    sent = []
    monkeypatch.setattr(app_module.messenger_api, "send_message",
                         lambda psid, text, token: sent.append((psid, text)))
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    response = _signed_post(client, "/messenger-webhook",
                             _messenger_payload(page_id, "psid-1", "Can I book a haircut?"),
                             "test-messenger-app-secret")
    assert response.status_code == 200
    assert calls[0]["channel"] == "messenger"
    assert sent == [("psid-1", "Sure thing!")]

    conversations = database.conversations_for_business(business_id)
    assert len(conversations) == 1
    assert conversations[0][1] == "psid-1"  # Messenger has no display-name resolution - falls back to the PSID


def test_webhook_respects_auto_send_disabled(client, unique_username, unique_email, monkeypatch):
    """Traps app.py:2712's gate: web/dashboard auto-send-off must still leave a normal
    pending draft for Meta channels, not fail to save at all."""
    _, business_id = register_and_login(client, unique_username, unique_email)
    ig_account_id = f"ig-{uuid.uuid4().hex[:10]}"
    database.link_instagram_account(business_id, ig_account_id, "the_biz", "fake-token")
    database.set_auto_send(business_id, False)

    _stub_ai(monkeypatch, ["We're open 9-6."])
    monkeypatch.setattr(app_module, "get_sender_info", lambda igsid, token: {
        "display_name": "jane_doe", "profile_pic_url": None,
    })
    sent = []
    monkeypatch.setattr(app_module, "send_instagram_message", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(ai_replies, "send_push_to_business", lambda *a, **k: None)

    _signed_post(client, "/instagram-webhook",
                 _instagram_payload(ig_account_id, "igsid-1", "Are you open?"),
                 "test-instagram-app-secret")

    assert sent == []
    conversations = database.conversations_for_business(business_id)
    assert len(conversations) == 1
    assert conversations[0][6] == "pending"  # status column - draft waiting for manual approval
