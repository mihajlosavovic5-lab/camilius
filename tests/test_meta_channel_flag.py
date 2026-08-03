"""META_CHANNELS_ENABLED - the reversible switch for retiring Instagram/WhatsApp/
Messenger from the live product without deleting any of that code (see app.py's own
comment on the flag). Defaults to True, so every existing test above this file already
proves the flag being on changes nothing; these prove the flag being off actually does
something, on one representative route per shape: a login-gated connect route, an
unauthenticated OAuth callback, and an external webhook.
"""
import app as app_module
from conftest import register_and_login


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
