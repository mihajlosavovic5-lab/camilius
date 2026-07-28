"""Tests for the /avatar (web) and /api/conversations/<id>/avatar (app) endpoints -
these re-resolve a sender's Instagram profile picture live via the Graph API instead of
trusting the copy stored on the conversation at message-receipt time, which is a signed
Meta CDN link that eventually expires. See conversation_avatar()/api_conversation_avatar()
in app.py/api.py and their <img onerror> callers in dashboard.html/mobile/app.js."""
import uuid

import api as api_module
import app as app_module
import database
from conftest import register_and_login, register_business_web


def _connect_instagram(business_id):
    database.link_instagram_account(
        business_id, f"ig-account-{uuid.uuid4().hex[:10]}", "connected_biz", "fake-access-token",
    )


def test_api_avatar_returns_fresh_url(client, unique_username, unique_email, monkeypatch):
    token, business_id = register_and_login(client, unique_username, unique_email)
    _connect_instagram(business_id)
    conversation_id = database.save_draft_reply(
        business_id, "real_ig_user", "Is this available?", "Yes!", sender_igsid="igsid-1",
        profile_pic_url="https://scontent.example.com/expired.jpg",
    )

    monkeypatch.setattr(api_module, "get_sender_info", lambda sender_igsid, access_token: {
        "display_name": "real_ig_user", "profile_pic_url": "https://scontent.example.com/fresh.jpg",
    })

    response = client.get(f"/api/conversations/{conversation_id}/avatar",
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["profile_pic_url"] == "https://scontent.example.com/fresh.jpg"


def test_api_avatar_404s_when_graph_api_has_no_picture(client, unique_username, unique_email, monkeypatch):
    token, business_id = register_and_login(client, unique_username, unique_email)
    _connect_instagram(business_id)
    conversation_id = database.save_draft_reply(
        business_id, "real_ig_user", "Is this available?", "Yes!", sender_igsid="igsid-1",
    )

    monkeypatch.setattr(api_module, "get_sender_info", lambda sender_igsid, access_token: {
        "display_name": None, "profile_pic_url": None,
    })

    response = client.get(f"/api/conversations/{conversation_id}/avatar",
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_api_avatar_requires_ownership(client, unique_username, unique_email, monkeypatch):
    token_a, business_a = register_and_login(client, unique_username, unique_email)
    _connect_instagram(business_a)
    conversation_id = database.save_draft_reply(
        business_a, "real_ig_user", "Is this available?", "Yes!", sender_igsid="igsid-1",
    )

    other_token, _ = register_and_login(client, unique_username + "_2", "other_" + unique_email)

    response = client.get(f"/api/conversations/{conversation_id}/avatar",
                           headers={"Authorization": f"Bearer {other_token}"})
    assert response.status_code == 403


def test_web_avatar_redirects_to_fresh_url(client, unique_username, unique_email, monkeypatch):
    reg = register_business_web(client, unique_username, unique_email)
    assert reg.status_code == 302
    business = database.business_by_username(unique_username)
    business_id = business[0]
    _connect_instagram(business_id)

    conversation_id = database.save_draft_reply(
        business_id, "real_ig_user", "Is this available?", "Yes!", sender_igsid="igsid-1",
        profile_pic_url="https://scontent.example.com/expired.jpg",
    )

    monkeypatch.setattr(app_module, "get_sender_info", lambda sender_igsid, access_token: {
        "display_name": "real_ig_user", "profile_pic_url": "https://scontent.example.com/fresh.jpg",
    })

    response = client.get(f"/avatar/{conversation_id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == "https://scontent.example.com/fresh.jpg"


def test_web_avatar_404s_without_sender_igsid(client, unique_username, unique_email):
    reg = register_business_web(client, unique_username, unique_email)
    assert reg.status_code == 302
    business = database.business_by_username(unique_username)
    business_id = business[0]
    _connect_instagram(business_id)

    conversation_id = database.save_draft_reply(business_id, "real_ig_user", "Is this available?", "Yes!")

    response = client.get(f"/avatar/{conversation_id}")
    assert response.status_code == 404
