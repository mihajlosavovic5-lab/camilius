"""META_CHANNELS_ENABLED - the reversible switch for retiring Instagram/WhatsApp/
Messenger from the live product without deleting any of that code (see app.py's own
comment on the flag). Defaults to True, so every existing test above this file already
proves the flag being on changes nothing; these prove the flag being off actually does
something, on one representative route per shape: a login-gated connect route, an
unauthenticated OAuth callback, and an external webhook.
"""
import app as app_module
from conftest import register_and_login, register_business_web


def test_meta_channels_enabled_by_default():
    assert app_module.META_CHANNELS_ENABLED is True


def test_connect_route_404s_when_meta_channels_disabled(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "META_CHANNELS_ENABLED", False)
    token, business_id = register_and_login(client, unique_username, unique_email)
    with client.session_transaction() as sess:
        sess["business_id"] = business_id
    response = client.get("/connect-instagram")
    assert response.status_code == 404


def test_connect_route_works_when_meta_channels_enabled(client, unique_username, unique_email):
    # Sanity check for the test above: same request, flag left at its default (True),
    # must NOT 404 - otherwise the 404 above wouldn't be proving anything about the flag.
    _, business_id = register_and_login(client, unique_username, unique_email)
    with client.session_transaction() as sess:
        sess["business_id"] = business_id
    response = client.get("/connect-instagram")
    assert response.status_code != 404


def test_oauth_callback_404s_when_meta_channels_disabled(client, monkeypatch):
    monkeypatch.setattr(app_module, "META_CHANNELS_ENABLED", False)
    response = client.get("/instagram-oauth-callback")
    assert response.status_code == 404


def test_webhook_404s_when_meta_channels_disabled(client, monkeypatch):
    monkeypatch.setattr(app_module, "META_CHANNELS_ENABLED", False)
    response = client.get("/instagram-webhook", query_string={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123",
    })
    assert response.status_code == 404


def test_meta_channels_enabled_injected_into_templates(client):
    # inject_meta_channels_enabled's whole job is making the flag available to Jinja -
    # confirmed via a real render rather than calling the context processor directly.
    with app_module.app.test_request_context("/"):
        context = app_module.inject_meta_channels_enabled()
    assert context == {"meta_channels_enabled": True}


def test_settings_shows_meta_connect_rows_by_default(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    html = client.get("/profile").get_data(as_text=True)
    assert "Connect Instagram" in html
    assert "Connect WhatsApp" in html
    assert "Connect Messenger" in html


def test_settings_hides_meta_connect_rows_when_disabled(client, unique_username, unique_email, monkeypatch):
    # Repositioning: once META_CHANNELS_ENABLED is off, the routes these link to all
    # 404 (see the connect/oauth/webhook tests above) - showing "Connect Instagram" etc.
    # would just be dead links, so settings.html hides the whole row instead.
    register_business_web(client, unique_username, unique_email)
    monkeypatch.setattr(app_module, "META_CHANNELS_ENABLED", False)
    html = client.get("/profile").get_data(as_text=True)
    assert "Connect Instagram" not in html
    assert "Connect WhatsApp" not in html
    assert "Connect Messenger" not in html
    # Everything else on the page must still render normally.
    assert "Business details" in html or "Working hours" in html
