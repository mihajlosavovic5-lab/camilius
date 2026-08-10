import re
import uuid

import api as api_module
import app as app_module
from conftest import register_and_login


def _register_with_phone(client, unique_email):
    phone = f"+3816{uuid.uuid4().hex[:8]}"
    token, business_id = register_and_login(client, unique_email, phone_number=phone)
    return token, business_id, phone


def test_verify_phone_send_falls_back_to_email_without_twilio_configured(client, unique_email):
    """No TWILIO_* env vars are set in this test environment (see .env.example), so
    send_sms.sms_configured() is False and /verify-phone/send must stay on the emailed
    link it's always used - the whole point of gating the SMS path behind that check is
    that nothing here changes until real Twilio credentials are in place."""
    token, business_id, phone = _register_with_phone(client, unique_email)
    response = client.post("/api/verify-phone/send", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["channel"] == "email"


def test_verify_phone_send_uses_sms_when_configured(client, unique_email, monkeypatch):
    sent = {}

    def fake_send_sms(to_number, body):
        sent["to"] = to_number
        sent["body"] = body

    monkeypatch.setattr(api_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "send_sms", fake_send_sms)

    token, business_id, phone = _register_with_phone(client, unique_email)
    response = client.post("/api/verify-phone/send", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["channel"] == "sms"
    assert sent["to"] == phone
    assert re.search(r"\b\d{6}\b", sent["body"])


def test_verify_phone_confirm_accepts_the_texted_code(client, unique_email, monkeypatch):
    sent = {}
    monkeypatch.setattr(api_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "send_sms", lambda to_number, body: sent.update(to=to_number, body=body))

    token, business_id, phone = _register_with_phone(client, unique_email)
    client.post("/api/verify-phone/send", headers={"Authorization": f"Bearer {token}"})
    code = re.search(r"\b(\d{6})\b", sent["body"]).group(1)

    confirm = client.post(
        "/api/verify-phone/confirm", json={"code": code}, headers={"Authorization": f"Bearer {token}"}
    )
    assert confirm.status_code == 200
    assert confirm.get_json()["verified"] is True

    settings = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert settings.get_json()["phone_verified"] is True


def test_verify_phone_confirm_rejects_wrong_code(client, unique_email, monkeypatch):
    monkeypatch.setattr(api_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "sms_configured", lambda: True)
    monkeypatch.setattr(app_module, "send_sms", lambda to_number, body: None)

    token, business_id, phone = _register_with_phone(client, unique_email)
    client.post("/api/verify-phone/send", headers={"Authorization": f"Bearer {token}"})

    confirm = client.post(
        "/api/verify-phone/confirm", json={"code": "000000"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert confirm.status_code == 400

    settings = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert settings.get_json()["phone_verified"] is False
