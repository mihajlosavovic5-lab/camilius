import app as app_module
from conftest import register_business


def test_forgot_password_reports_unknown_email(client):
    # Deliberate product choice: tell the user directly that the email isn't registered,
    # rather than a generic "if that email is registered..." hedge.
    response = client.post("/api/forgot-password", json={"identifier": "definitely-not-registered@example.com"})
    assert response.status_code == 404
    assert "No account found" in response.get_json()["error"]


def test_forgot_password_confirms_known_email(client, unique_email, monkeypatch):
    # Avoid a real network call to Resend - only the response shape matters here, not
    # actual delivery (that's the transactional-email provider's job, not ours to re-test).
    monkeypatch.setattr(app_module, "send_email", lambda **kwargs: None)

    register_business(client, unique_email)

    # api_forgot_password() reads "identifier" (matches the mobile app's own request
    # body, see mobile/app.js) - not "email", which was this test's original bug.
    response = client.post("/api/forgot-password", json={"identifier": unique_email})
    assert response.status_code == 200
    assert "sent a password reset link" in response.get_json()["message"]
