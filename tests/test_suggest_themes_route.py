"""Tests for POST /profile/suggest-themes - Settings' "Suggest AI designs for my
business" button (see ai_theme.py and settings.html's own script block). AJAX-only:
returns theme option JSON, never saves anything (see save_business_page for the route
that actually persists a chosen theme)."""
import json

import ai_theme
from conftest import register_business_web, web_post, get_csrf_token


_AI_THEME_OPTION = {
    "label": "Calm Clinic", "blurb": "A calm, professional look.", "swatch": ["#111111", "#ffffff", "#222222"],
    "radius": "8px", "heading_font": None, "fixed_dark": False,
    "base": {"primary": "#111111", "on_primary": "#ffffff", "gold": "#222222", "background": "#ffffff",
             "surface": "#ffffff", "text": "#111111", "text_muted": "#666666", "border": "#dddddd"},
    "dark": None,
}


def test_suggest_themes_returns_options(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    monkeypatch.setattr(ai_theme, "generate_theme_options", lambda *args, **kwargs: [_AI_THEME_OPTION])
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/suggest-themes", {"csrf_token": csrf_token, "description": "A dentist's office."})
    assert response.status_code == 200
    data = response.get_json()
    assert data["themes"][0]["label"] == "Calm Clinic"


def test_suggest_themes_requires_description(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/suggest-themes", {"csrf_token": csrf_token, "description": "   "})
    assert response.status_code == 400
    assert "Describe your business" in response.get_json()["error"]


def test_suggest_themes_reports_error_when_ai_returns_nothing(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    monkeypatch.setattr(ai_theme, "generate_theme_options", lambda *args, **kwargs: [])
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/suggest-themes", {"csrf_token": csrf_token, "description": "A shop."})
    assert response.status_code == 400


def test_suggest_themes_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = web_post(client, "/profile/suggest-themes", {"csrf_token": csrf_token, "description": "A shop."})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
