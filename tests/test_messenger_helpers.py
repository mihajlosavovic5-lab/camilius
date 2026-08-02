import hashlib
import hmac

from messenger_api import verify_webhook_signature, verify_webhook_challenge, parse_webhook_messages


def test_webhook_signature_accepts_correct_signature():
    body = b'{"test": true}'
    secret = "test-messenger-app-secret"  # matches conftest's MESSENGER_APP_SECRET
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature) is True


def test_webhook_signature_rejects_wrong_signature():
    assert verify_webhook_signature(b'{"test": true}', "sha256=" + "0" * 64) is False


def test_webhook_signature_rejects_missing_signature():
    assert verify_webhook_signature(b"{}", None) is False
    assert verify_webhook_signature(b"{}", "") is False
    assert verify_webhook_signature(b"{}", "not-even-the-right-format") is False


def test_webhook_challenge_accepts_correct_token():
    # matches conftest's MESSENGER_WEBHOOK_VERIFY_TOKEN
    assert verify_webhook_challenge("subscribe", "test-messenger-verify-token", "echo-me-back") == "echo-me-back"


def test_webhook_challenge_rejects_wrong_token():
    assert verify_webhook_challenge("subscribe", "wrong-token", "echo-me-back") is None


def test_parse_webhook_messages_extracts_text_message():
    payload = {
        "object": "page",
        "entry": [{
            "id": "page-id-123",
            "time": 1735689600,
            "messaging": [{
                "sender": {"id": "psid-456"},
                "recipient": {"id": "page-id-123"},
                "timestamp": 1735689600,
                "message": {"mid": "m_abc123", "text": "Do you have a Saturday slot?"},
            }],
        }],
    }
    messages = parse_webhook_messages(payload)
    assert len(messages) == 1
    assert messages[0] == {
        "page_id": "page-id-123",
        "sender_psid": "psid-456",
        "message_id": "m_abc123",
        "text": "Do you have a Saturday slot?",
    }


def test_parse_webhook_messages_skips_echoes():
    # Messenger also delivers a webhook event for messages the Page itself sent
    # (is_echo=true) - must not be reprocessed as a new customer message.
    payload = {
        "entry": [{
            "id": "page-id-123",
            "messaging": [{
                "sender": {"id": "page-id-123"},
                "recipient": {"id": "psid-456"},
                "message": {"mid": "m_echo1", "text": "We're open!", "is_echo": True},
            }],
        }],
    }
    assert parse_webhook_messages(payload) == []


def test_parse_webhook_messages_skips_non_text_messages():
    payload = {
        "entry": [{
            "id": "page-id-123",
            "messaging": [{
                "sender": {"id": "psid-456"},
                "message": {"mid": "m_xyz", "attachments": [{"type": "image"}]},
            }],
        }],
    }
    assert parse_webhook_messages(payload) == []
