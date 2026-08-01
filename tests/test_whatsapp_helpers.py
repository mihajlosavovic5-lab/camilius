import hashlib
import hmac

from whatsapp_api import verify_webhook_signature, verify_webhook_challenge, parse_webhook_messages


def test_webhook_signature_accepts_correct_signature():
    body = b'{"test": true}'
    secret = "test-whatsapp-app-secret"  # matches conftest's WHATSAPP_APP_SECRET
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature) is True


def test_webhook_signature_rejects_wrong_signature():
    assert verify_webhook_signature(b'{"test": true}', "sha256=" + "0" * 64) is False


def test_webhook_signature_rejects_missing_signature():
    assert verify_webhook_signature(b"{}", None) is False
    assert verify_webhook_signature(b"{}", "") is False
    assert verify_webhook_signature(b"{}", "not-even-the-right-format") is False


def test_webhook_challenge_accepts_correct_token():
    # matches conftest's WHATSAPP_WEBHOOK_VERIFY_TOKEN
    assert verify_webhook_challenge("subscribe", "test-whatsapp-verify-token", "echo-me-back") == "echo-me-back"


def test_webhook_challenge_rejects_wrong_token():
    assert verify_webhook_challenge("subscribe", "wrong-token", "echo-me-back") is None


def test_webhook_challenge_rejects_wrong_mode():
    assert verify_webhook_challenge("unsubscribe", "test-whatsapp-verify-token", "echo-me-back") is None


def test_parse_webhook_messages_extracts_text_message_with_sender_name():
    payload = {
        "entry": [{
            "id": "waba-id",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "381601234567", "phone_number_id": "123456789012345"},
                    "contacts": [{"profile": {"name": "Ana"}, "wa_id": "381601111111"}],
                    "messages": [{
                        "from": "381601111111",
                        "id": "wamid.abc123",
                        "timestamp": "1735689600",
                        "type": "text",
                        "text": {"body": "Do you have a Saturday slot?"},
                    }],
                },
            }],
        }],
    }
    messages = parse_webhook_messages(payload)
    assert len(messages) == 1
    assert messages[0] == {
        "phone_number_id": "123456789012345",
        "sender_wa_id": "381601111111",
        "message_id": "wamid.abc123",
        "sender_name": "Ana",
        "text": "Do you have a Saturday slot?",
    }


def test_parse_webhook_messages_skips_status_updates():
    # Delivery/read receipts arrive on the same webhook under value.statuses, not
    # value.messages - must not be mistaken for a new customer message.
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "123456789012345"},
                    "statuses": [{"id": "wamid.abc123", "status": "delivered"}],
                },
            }],
        }],
    }
    assert parse_webhook_messages(payload) == []


def test_parse_webhook_messages_skips_non_text_messages():
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "123456789012345"},
                    "contacts": [{"profile": {"name": "Ana"}, "wa_id": "381601111111"}],
                    "messages": [{"from": "381601111111", "id": "wamid.xyz", "type": "image", "image": {"id": "media-id"}}],
                },
            }],
        }],
    }
    assert parse_webhook_messages(payload) == []


def test_parse_webhook_messages_handles_no_matching_contact():
    # A message can arrive without a matching entry in `contacts` (seen in practice for
    # some message types) - falls back to no sender_name rather than raising.
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "123456789012345"},
                    "messages": [{"from": "381601111111", "id": "wamid.xyz", "type": "text", "text": {"body": "Hi"}}],
                },
            }],
        }],
    }
    messages = parse_webhook_messages(payload)
    assert len(messages) == 1
    assert messages[0]["sender_name"] is None
