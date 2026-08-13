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

import pytest

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


# --- translate_page.translation_is_usable (unit) ---

def test_translation_is_usable_true_when_every_source_field_is_covered():
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "a", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "A", "P", "F") is True


def test_translation_is_usable_false_when_a_nonempty_source_field_translated_empty():
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "A", "P", "F") is False


def test_translation_is_usable_true_when_an_empty_source_field_stays_empty():
    # Nothing to translate for a field the business never filled in - an empty result
    # there is correct, not degenerate.
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "", "P", "F") is True


def test_translation_is_usable_false_when_a_ui_string_is_missing():
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    ui["chat_send"] = ""
    result = {"tagline": "t", "about": "a", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "A", "P", "F") is False


def test_translation_is_usable_false_when_about_is_echoed_back_untranslated():
    # The model returned the source text completely unchanged - a real translation
    # essentially never looks byte-for-byte identical to its source, so this is a
    # failed/echoed response, not a translation that just happens to read the same.
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "Radimo od 2019. godine.", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "Radimo od 2019. godine.", "P", "F") is False


def test_translation_is_usable_false_even_when_the_target_matches_the_source_language():
    # This used to be exempt (a visitor picking the flag that already matches the
    # business's own content is a legitimate no-op) via an explicit lang == source_lang
    # check - dropped because it depended on content_language being correct, and a
    # business whose content_language was ever wrong or stale had this whole check
    # silently disabled for exactly the one language matching that value, forever (see
    # this function's own docstring). The rare cost - a same-language click 502s instead
    # of silently no-op'ing - is a better trade than a language being invisibly broken.
    ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "Radimo od 2019. godine.", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "Radimo od 2019. godine.", "P", "F") is False


def test_translation_is_usable_true_when_a_loanword_heavy_price_list_survives_unchanged():
    # The echo check briefly covered price_list/tagline too (see git history), but a real
    # price list routinely uses loanwords ("Espresso", "Cappuccino") that a genuinely
    # correct translation leaves unchanged in most target languages - flagging that as a
    # failure rejected good translations outright, for every language, on every request
    # for a business whose menu happens to read this way. price_list is exempt from the
    # echo check for exactly this reason; only "about" (free-form prose) is checked.
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    source_price_list = "Espresso: 20 EUR\nCappuccino: 10 EUR\nLatte: 15 EUR"
    result = {"tagline": "Aroma", "about": "Traducción real, distinta del origen.", "price_list": source_price_list, "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "Aroma", "Radimo od 2019. godine.", source_price_list, "F") is True


def test_translation_is_usable_true_when_a_short_about_survives_translation_unchanged():
    # A bare/very short "about" can legitimately come back identical - only a long enough
    # one counts as a suspected echo.
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": "a", "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", "a", "P", "F") is True


def test_translate_page_content_rejects_a_response_missing_real_content(monkeypatch):
    # A response that parses fine but is missing translated text for a field the source
    # actually had - the exact shape a truncated-but-still-schema-valid model reply could
    # take - must surface as the same ValueError a flat-out failed call would, not as a
    # "successful" result the caller would go on to cache.
    class _FakeMessage:
        content = json.dumps({
            "tagline": "t", "about": "", "price_list": "p", "faq": "f",
            "ui": dict(translate_page.PAGE_UI_STRINGS),
        })

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    monkeypatch.setattr(translate_page.client.chat.completions, "create", lambda **kwargs: _FakeResponse())

    with pytest.raises(ValueError):
        translate_page.translate_page_content("es", "T", "A", "P", "F")


def test_translate_endpoint_reruns_the_model_when_the_cached_row_is_degenerate(client, unique_email, monkeypatch):
    # A row cached before translation_is_usable() existed (or that slipped past it) must
    # not keep serving the same broken result to every future visitor forever - the
    # endpoint re-translates instead of trusting a cache hit that fails the same check a
    # fresh result would.
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    stale_ui = json.dumps(dict(translate_page.PAGE_UI_STRINGS))
    database.upsert_page_translation(business_id, "es", "[es] Fresh cuts daily", "", "[es] Haircut: 20 EUR", "[es] Q\nA", stale_ui)

    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 200
    assert response.get_json()["about"] == "[es] We opened in 2019."
    assert calls == ["es"]

    # The regenerated result overwrote the stale row - a later request is now served from
    # cache again, without a second model call.
    response = client.get(f"/p/{slug}/translate/es")
    assert response.get_json()["about"] == "[es] We opened in 2019."
    assert calls == ["es"]


def test_translate_endpoint_reruns_the_model_when_the_cached_row_echoed_the_source(client, unique_email, monkeypatch):
    # Reproduces the exact reported bug: a business whose content is written in Serbian
    # had its "es" translation cached at some point with the Serbian "about" echoed back
    # untranslated instead of actually translated - every later visitor picking the
    # Spanish flag saw Serbian, forever, since a cache hit never re-called the model.
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    stale_ui = json.dumps(dict(translate_page.PAGE_UI_STRINGS))
    # "We opened in 2019." (the real, un-translated source "about" from _publish_page)
    # cached verbatim under the "es" key - looks like a normal, fully-populated row, but
    # is actually the un-translated source, not a Spanish translation.
    database.upsert_page_translation(business_id, "es", "Fresh cuts daily", "We opened in 2019.", "Haircut: 20 EUR", "Q\nA", stale_ui)

    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 200
    assert response.get_json()["about"] == "[es] We opened in 2019."
    assert calls == ["es"]
