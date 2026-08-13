"""Tests for the public page's header language switcher and its backing
GET /p/<slug>/translate/<lang> endpoint - see app.py's public_page()/
public_page_translate and translate_page.py. Covers: the switcher's fixed EN/ES/FR/DE
flag row plus an extra, leading flag for whatever language the business's own content
is detected to be written in, the translate endpoint's caching (only the first request
for a given (business, language) pair ever calls OpenAI), and its gating (unknown
language, unpublished page).
"""
import json
import uuid

import database
import translate_page
from conftest import register_business_web


def _publish_page(client, unique_email):
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"tr-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", "We opened in 2019.")
    return business_id, slug


def _fake_translation(lang):
    ui = dict(translate_page.PAGE_UI_STRINGS)
    ui = {key: f"[{lang}] {value}" for key, value in ui.items()}
    return {"tagline": f"[{lang}] Fresh cuts daily", "about": f"[{lang}] We opened in 2019.", "price_list": f"[{lang}] Haircut: 20 EUR", "faq": f"[{lang}] Q\nA", "ui": ui}


def test_public_page_shows_default_language_flags(client, unique_email):
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert 'id="lang-btn"' in html
    assert 'id="lang-dropdown"' in html
    for code in translate_page.DEFAULT_PAGE_LANGUAGES:
        assert f'data-lang="{code}"' in html


def test_public_page_adds_detected_content_language_flag(client, unique_email, monkeypatch):
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "sr")
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert 'data-lang="sr"' in html
    assert "rs.png" in html
    page = database.get_business_page(business_id)
    assert page[16] == "sr"


def test_public_page_lists_detected_content_language_flag_first(client, unique_email, monkeypatch):
    # The detected flag leads the row rather than trailing after the fixed EN/ES/FR/DE
    # four - it's the one language guaranteed to actually match this business's content.
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "sr")
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert html.index('data-lang="sr"') < html.index('data-lang="en"')


def test_content_language_is_detected_only_once(client, unique_email, monkeypatch):
    calls = []
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: calls.append(text) or "sr")
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    client.get(f"/p/{slug}")
    client.get(f"/p/{slug}")
    assert len(calls) == 1


def test_public_page_does_not_duplicate_a_detected_default_language(client, unique_email, monkeypatch):
    # es is already in DEFAULT_PAGE_LANGUAGES - detecting it too shouldn't add a second
    # "es" flag to the switcher.
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "es")
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert html.count('data-lang="es"') == 1


def test_translate_endpoint_returns_translated_content_and_caches(client, unique_email, monkeypatch):
    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 200
    data = response.get_json()
    assert data["tagline"] == "[es] Fresh cuts daily"
    assert data["ui"]["heading_about"] == "[es] About"
    assert calls == ["es"]

    # Second request for the same (business, lang) is served from the cache - the fake
    # translate function is never called again.
    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 200
    assert response.get_json()["tagline"] == "[es] Fresh cuts daily"
    assert calls == ["es"]

    cached = database.get_page_translation(business_id, "es")
    assert cached is not None
    assert cached[0] == "[es] Fresh cuts daily"
    assert json.loads(cached[4])["heading_about"] == "[es] About"


def test_translate_endpoint_404s_for_unsupported_language(client, unique_email):
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    response = client.get(f"/p/{slug}/translate/xx")
    assert response.status_code == 404


def test_translate_endpoint_404s_for_unpublished_page(client, unique_email):
    # Same bare `page[3]` gate as public_page_chat - real OpenAI spend, so even the
    # owner's own logged-in session (register_business_web leaves the client logged in)
    # previewing its own still-unpublished page doesn't get a pass here.
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, "Draft tagline", None)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 404


def test_translate_endpoint_404s_for_unknown_slug(client):
    response = client.get(f"/p/does-not-exist-{uuid.uuid4().hex[:8]}/translate/es")
    assert response.status_code == 404


def test_translate_endpoint_502s_when_the_model_call_fails(client, unique_email, monkeypatch):
    def fake_translate(lang, tagline, about, price_list, faq):
        raise ValueError("model returned no content")

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 502
