import hashlib
import hmac

from api import business_id_from_instagram_connect_state, make_instagram_connect_state
from instagram_api import verify_webhook_signature


def test_webhook_signature_accepts_correct_signature():
    body = b'{"test": true}'
    secret = "test-instagram-app-secret"  # matches conftest's INSTAGRAM_APP_SECRET
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature) is True


def test_webhook_signature_rejects_wrong_signature():
    assert verify_webhook_signature(b'{"test": true}', "sha256=" + "0" * 64) is False


def test_webhook_signature_rejects_missing_signature():
    assert verify_webhook_signature(b"{}", None) is False
    assert verify_webhook_signature(b"{}", "") is False
    assert verify_webhook_signature(b"{}", "not-even-the-right-format") is False


def test_instagram_connect_state_roundtrip():
    token = make_instagram_connect_state(business_id=42)
    assert business_id_from_instagram_connect_state(token) == 42


def test_instagram_connect_state_rejects_garbage():
    assert business_id_from_instagram_connect_state("not-a-real-token") is None
    assert business_id_from_instagram_connect_state(None) is None
    assert business_id_from_instagram_connect_state("") is None
