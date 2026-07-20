import app as app_module
from conftest import register_business


def test_forgot_password_generic_message_for_unknown_email(client):
    response = client.post("/api/forgot-password", json={"email": "definitely-not-registered@example.com"})
    assert response.status_code == 200
    assert "sent a password reset link" in response.get_json()["message"]


def test_forgot_password_generic_message_for_known_email(client, unique_username, unique_email, monkeypatch):
    # Avoid a real network call to Resend - only the response shape matters here, not
    # actual delivery (that's the transactional-email provider's job, not ours to re-test).
    monkeypatch.setattr(app_module, "send_email", lambda **kwargs: None)

    register_business(client, unique_username, unique_email)

    response = client.post("/api/forgot-password", json={"email": unique_email})
    assert response.status_code == 200
    # Same message regardless of whether the account exists - must not leak that.
    assert "sent a password reset link" in response.get_json()["message"]
