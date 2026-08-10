"""Tests for the mobile app's Bearer-authed My Site tab endpoints (api.py): /api/page,
/api/generate-page, /api/voice-transcribe, /api/suggest-themes - Bearer equivalents of the
website's session-authed /profile/page, /profile/generate-page,
/api/onboarding/voice-transcribe, /profile/suggest-themes. OpenAI calls are monkeypatched,
same "never make a real API call" rule as the rest of the suite."""
import json
import uuid

import database
import themes
from conftest import register_and_login
from tests.test_page_generator import VALID_DRAFT, _FakeResponse
from tests.test_onboarding_voice import _mock_transcription
from tests.test_ai_theme import VALID_THEMES_RESPONSE

import page_generator
import ai_theme


def _auth(client, email):
    token, business_id = register_and_login(client, email)
    return {"Authorization": f"Bearer {token}"}, business_id


# --- /api/page ---

def test_get_page_returns_none_when_no_page_saved(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.get("/api/page", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["page"] is None
    assert data["upcoming_appointments"] == []
    assert data["preferred_theme"] is None
    assert data["preferred_custom_theme_json"] is None


def test_save_and_get_page(client, unique_email):
    headers, business_id = _auth(client, unique_email)
    slug = f"cool-business-{uuid.uuid4().hex[:8]}"
    response = client.post("/api/page", json={
        "slug": slug, "tagline": "Fresh cuts", "about": "A great barbershop.",
        "published": True, "theme": "minimal",
    }, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/api/page", headers=headers)
    page = get_response.get_json()["page"]
    assert page["slug"] == slug
    assert page["tagline"] == "Fresh cuts"
    assert page["published"] is True
    assert page["theme"] == "minimal"


def test_save_page_rejects_invalid_slug(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/page", json={"slug": "Not A Valid Slug!"}, headers=headers)
    assert response.status_code == 400


# --- Design picked before a web address is ever set (see api_save_page's blank-slug
# branch and database.set_preferred_theme) ---

def test_save_page_persists_theme_preference_with_blank_slug_and_no_page(client, unique_email):
    headers, business_id = _auth(client, unique_email)
    response = client.post("/api/page", json={"slug": "", "theme": "bold"}, headers=headers)
    assert response.status_code == 200
    assert database.get_business_page(business_id) is None
    assert database.business_by_id(business_id)[22] == "bold"


def test_get_page_reflects_theme_preference_when_no_page(client, unique_email):
    headers, _ = _auth(client, unique_email)
    client.post("/api/page", json={"slug": "", "theme": "bold"}, headers=headers)

    response = client.get("/api/page", headers=headers)
    data = response.get_json()
    assert data["page"] is None
    assert data["preferred_theme"] == "bold"


def test_save_page_with_non_blank_invalid_slug_still_400s(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/page", json={"slug": "Not A Valid Slug!", "theme": "bold"}, headers=headers)
    assert response.status_code == 400


def test_save_page_theme_preference_then_real_page_created_normally(client, unique_email):
    headers, business_id = _auth(client, unique_email)
    client.post("/api/page", json={"slug": "", "theme": "bold"}, headers=headers)

    slug = f"cool-business-{uuid.uuid4().hex[:8]}"
    response = client.post("/api/page", json={"slug": slug, "theme": "bold"}, headers=headers)
    assert response.status_code == 200

    page_row = database.get_business_page(business_id)
    assert page_row is not None
    assert page_row[2] == slug
    assert page_row[9] == "bold"


def test_save_page_with_custom_theme_json(client, unique_email):
    headers, business_id = _auth(client, unique_email)
    custom = {
        "label": "My Custom Look", "blurb": "x", "swatch": ["#111111", "#ffffff", "#222222"],
        "radius": "8px", "heading_font": None, "fixed_dark": False,
        "base": {"primary": "#111111", "on_primary": "#ffffff", "gold": "#222222", "background": "#ffffff",
                 "surface": "#ffffff", "text": "#111111", "text_muted": "#666666", "border": "#dddddd"},
        "dark": None,
    }
    response = client.post("/api/page", json={
        "slug": f"custom-theme-biz-{uuid.uuid4().hex[:8]}", "theme": "custom", "custom_theme_json": json.dumps(custom),
    }, headers=headers)
    assert response.status_code == 200

    page_row = database.get_business_page(business_id)
    assert page_row[9] == themes.CUSTOM_THEME_ID
    resolved = themes.resolve_theme(page_row[9], page_row[10])
    assert resolved["label"] == "My Custom Look"


def test_page_requires_auth(client):
    assert client.get("/api/page").status_code == 401


# --- /api/generate-page ---

def test_generate_page_returns_draft_without_saving(client, unique_email, monkeypatch):
    headers, business_id = _auth(client, unique_email)
    original_hours = database.business_by_id(business_id)[8]
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_DRAFT)),
    )
    response = client.post("/api/generate-page", json={"description": "A barbershop, weekdays 9-6."}, headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["tagline"] == VALID_DRAFT["tagline"]
    assert data["about"] == VALID_DRAFT["about"]
    assert data["slug"] == VALID_DRAFT["slug"]
    assert database.business_by_id(business_id)[8] == original_hours  # nothing persisted
    assert database.get_business_page(business_id) is None


def test_generate_page_keeps_already_saved_slug(client, unique_email, monkeypatch):
    headers, business_id = _auth(client, unique_email)
    existing_slug = f"already-chosen-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, existing_slug, False, "Old tagline", "Old about")
    draft = dict(VALID_DRAFT, slug=f"a-totally-different-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(draft)),
    )
    response = client.post("/api/generate-page", json={"description": "A barbershop, weekdays 9-6."}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["slug"] == existing_slug


def test_generate_page_requires_description(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/generate-page", json={"description": "  "}, headers=headers)
    assert response.status_code == 400


# --- /api/check-domains ---

def test_check_domains_returns_results_for_each_default_tld(client, unique_email, monkeypatch):
    import domain_lookup
    monkeypatch.setattr(domain_lookup, "check_domain", lambda domain: domain_lookup.AVAILABLE)
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/check-domains", json={"name": "blue-star-cafe"}, headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["domains"] == [{"domain": f"blue-star-cafe{tld}", "status": domain_lookup.AVAILABLE} for tld in domain_lookup.DEFAULT_TLDS]


def test_check_domains_requires_a_name(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/check-domains", json={"name": "   "}, headers=headers)
    assert response.status_code == 400


def test_check_domains_requires_auth(client):
    assert client.post("/api/check-domains", json={"name": "blue-star-cafe"}).status_code == 401


def test_generate_page_requires_auth(client):
    assert client.post("/api/generate-page", json={"description": "A shop."}).status_code == 401


# --- /api/voice-transcribe ---

def test_voice_transcribe_returns_text(client, unique_email, monkeypatch):
    headers, _ = _auth(client, unique_email)
    _mock_transcription(monkeypatch, text="A cozy cafe downtown, open 8 to 22.")
    response = client.post(
        "/api/voice-transcribe",
        data={"audio": (__import__("io").BytesIO(b"fake-audio-bytes"), "recording.webm", "audio/webm")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()["text"] == "A cozy cafe downtown, open 8 to 22."


def test_voice_transcribe_requires_audio(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/voice-transcribe", data={}, content_type="multipart/form-data", headers=headers)
    assert response.status_code == 400


def test_voice_transcribe_requires_auth(client):
    assert client.post("/api/voice-transcribe", data={}, content_type="multipart/form-data").status_code == 401


# --- /api/suggest-themes ---

def test_suggest_themes_returns_options(client, unique_email, monkeypatch):
    headers, _ = _auth(client, unique_email)
    monkeypatch.setattr(
        ai_theme.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_THEMES_RESPONSE)),
    )
    response = client.post("/api/suggest-themes", json={"description": "A dentist's office."}, headers=headers)
    assert response.status_code == 200
    themes_out = response.get_json()["themes"]
    assert len(themes_out) == 3


def test_suggest_themes_requires_description(client, unique_email):
    headers, _ = _auth(client, unique_email)
    response = client.post("/api/suggest-themes", json={"description": ""}, headers=headers)
    assert response.status_code == 400


def test_suggest_themes_requires_auth(client):
    assert client.post("/api/suggest-themes", json={"description": "A shop."}).status_code == 401
