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

import app as app_module
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
    # "about" is the one field translation_is_usable checks for an echoed/near-echoed
    # source (see translate_page._looks_echoed) - genuinely different wording here, not
    # just the source with a "[lang]" tag stuck on front, so this fixture reads as an
    # actual translation instead of tripping that check on a short fixture string where a
    # short prefix is a big enough share of the total length to look suspicious.
    return {"tagline": f"[{lang}] Fresh cuts daily", "about": f"[{lang}] Contenido totalmente traducido, distinto del original.", "price_list": f"[{lang}] Haircut: 20 EUR", "faq": f"[{lang}] Q\nA", "ui": ui}


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


def test_translate_endpoint_logs_why_a_failed_translation_was_rejected(client, unique_email, monkeypatch):
    # A failed translation is otherwise completely silent to a visitor (the page's own
    # fetch().catch() just leaves them on whatever they were already looking at, on
    # purpose) - this is the one place the actual reason (translate_page.
    # translation_rejection_reason's own message) ever gets recorded anywhere, so a
    # language that keeps failing has something more to go on than "it doesn't work."
    def fake_translate(lang, tagline, about, price_list, faq):
        raise ValueError("model returned an unusable translation: about looks echoed (similarity=0.97)")

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 502

    errors = database.recent_client_errors(limit=10)
    matching = [e for e in errors if e[1] == business_id and e[4] == "public_page_translate"]
    assert len(matching) == 1
    assert "about looks echoed" in matching[0][2]


def test_translate_page_content_wraps_a_non_valueerror_model_failure(monkeypatch):
    # Anything the OpenAI call itself can raise (rate limit, timeout, a content-policy
    # refusal, ...) needs to come out as a ValueError - public_page_translate's own
    # `except ValueError` (and its log_client_error call) doesn't catch any other
    # exception type, so an unwrapped failure here would 500 with nothing logged.
    def fake_create(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(translate_page.client.chat.completions, "create", fake_create)

    with pytest.raises(ValueError, match="connection reset"):
        translate_page.translate_page_content("es", "T", "A", "P", "F")


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


def test_translation_is_usable_false_when_about_echo_differs_only_by_unicode_normalization():
    # A plain == comparison looked airtight but wasn't: Serbian Latin's š/đ/č/ć/ž (and
    # plenty of other languages' own diacritics) each have both a precomposed single
    # codepoint and a decomposed base-letter-plus-combining-mark encoding - visually and
    # semantically identical text, but a different sequence of codepoints, which a model
    # reply isn't guaranteed to preserve even when it's otherwise just echoing the source
    # straight back. This reproduces exactly that: same text, only the encoding differs.
    import unicodedata
    source = "Radimo već tri godine i pravimo domaću kafu i kolače svakog jutra."
    echoed_but_renormalized = unicodedata.normalize("NFD", source)
    assert echoed_but_renormalized != source  # sanity check that this actually exercises the gap
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": echoed_but_renormalized, "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", source, "P", "F") is False


def test_translation_is_usable_false_when_about_is_a_near_echo_with_a_minor_edit():
    # A model reply that leaves "about" almost entirely as-is - a token nudged here or
    # there, nothing resembling an actual translation - is just as much a failure as a
    # byte-for-byte echo, even though it isn't one; catching it needs a similarity check,
    # not exact equality.
    source = "Radimo već tri godine i pravimo domaću kafu i kolače svakog jutra u centru grada."
    near_echo = source + " Uvek ste dobrodošli."
    ui = {key: f"[es] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    result = {"tagline": "t", "about": near_echo, "price_list": "p", "faq": "f", "ui": ui}
    assert translate_page.translation_is_usable(result, "T", source, "P", "F") is False


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


def test_translate_page_content_retries_after_an_echoed_first_attempt(monkeypatch):
    # Production evidence showed a real, reproducible case of the model echoing "about"
    # untranslated on one attempt - chat completions aren't deterministic here, so a
    # second attempt at the same prompt is a genuine second chance, not a guaranteed
    # repeat of the first. The call should keep retrying (up to
    # translate_page._MAX_TRANSLATE_ATTEMPTS) and return the first attempt that's
    # actually usable, rather than giving up on the very first bad one.
    source_about = "Radimo već tri godine i pravimo domaću kafu i kolače svakog jutra."
    good_ui = dict(translate_page.PAGE_UI_STRINGS)

    def echoed_response(**kwargs):
        content = json.dumps({"tagline": "t", "about": source_about, "price_list": "p", "faq": "f", "ui": good_ui})
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

    def good_response(**kwargs):
        content = json.dumps({"tagline": "t", "about": "Una traducción real y distinta.", "price_list": "p", "faq": "f", "ui": good_ui})
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

    attempts = [echoed_response, good_response]

    def fake_create(**kwargs):
        return attempts.pop(0)(**kwargs)

    monkeypatch.setattr(translate_page.client.chat.completions, "create", fake_create)

    result = translate_page.translate_page_content("es", "T", source_about, "P", "F")
    assert result["about"] == "Una traducción real y distinta."
    assert attempts == []  # both attempts were consumed - it really did retry, not just succeed on the first


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
    assert response.get_json()["about"] == "[es] Contenido totalmente traducido, distinto del original."
    assert calls == ["es"]

    # The regenerated result overwrote the stale row - a later request is now served from
    # cache again, without a second model call.
    response = client.get(f"/p/{slug}/translate/es")
    assert response.get_json()["about"] == "[es] Contenido totalmente traducido, distinto del original."
    assert calls == ["es"]


def test_translate_endpoint_reruns_the_model_when_the_cached_row_json_is_malformed(client, unique_email, monkeypatch):
    # A cached row whose ui_json somehow isn't valid JSON used to crash this route
    # outright (an unhandled json.JSONDecodeError) instead of getting the same
    # "fall through and re-translate" treatment every other kind of bad cached row
    # already gets - from a visitor's side, a 500 and a 502 both look like a language
    # that silently does nothing, with nothing pointing at a corrupt row as the reason.
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    database.upsert_page_translation(business_id, "es", "Fresh cuts daily", "Real translation", "Haircut: 20 EUR", "Q\nA", "{not valid json")

    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)

    response = client.get(f"/p/{slug}/translate/es")
    assert response.status_code == 200
    assert response.get_json()["about"] == "[es] Contenido totalmente traducido, distinto del original."
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
    assert response.get_json()["about"] == "[es] Contenido totalmente traducido, distinto del original."
    assert calls == ["es"]


# --- _pretranslate_page_languages (app.py) - pre-warming business_page_translations so a
# visitor's first flag pick is a cache hit, instead of that visitor's own request paying
# for the live OpenAI call. Called directly here (not via the background-thread wrapper
# the real save routes use) so these tests stay synchronous and deterministic. ---

def test_pretranslate_fills_the_cache_for_every_default_language(client, unique_email, monkeypatch):
    # Content language deliberately outside DEFAULT_PAGE_LANGUAGES, so none of the four
    # fixed switcher languages get skipped as "this is the page's own language".
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "pt")
    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    app_module._pretranslate_page_languages(business_id)

    assert sorted(calls) == sorted(translate_page.DEFAULT_PAGE_LANGUAGES)
    for lang in translate_page.DEFAULT_PAGE_LANGUAGES:
        cached = database.get_page_translation(business_id, lang)
        assert cached is not None
        assert cached[0] == f"[{lang}] Fresh cuts daily"


def test_pretranslate_never_translates_into_the_detected_content_language(client, unique_email, monkeypatch):
    # "sr" isn't one of DEFAULT_PAGE_LANGUAGES, but it's still the page's own detected
    # language here - the switcher offers it as a flag (see _render_public_page's
    # page_languages), but its content is always seeded directly from the original
    # tagline/about/etc. client-side, never through this cache, so it must never be
    # requested from the translator even though it's outside the fixed four.
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "sr")
    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    app_module._pretranslate_page_languages(business_id)

    assert sorted(calls) == sorted(translate_page.DEFAULT_PAGE_LANGUAGES)
    assert "sr" not in calls
    assert database.get_page_translation(business_id, "sr") is None
    assert database.get_business_page(business_id)[16] == "sr"


def test_pretranslate_skips_the_content_language_itself(client, unique_email, monkeypatch):
    # "es" is both a DEFAULT_PAGE_LANGUAGES entry and (here) the detected content
    # language - translating the page into the language it's already written in is a
    # wasted call (and would fail translation_is_usable's own echo check), so it must be
    # skipped rather than requested like every other target language.
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "es")
    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    app_module._pretranslate_page_languages(business_id)

    assert "es" not in calls
    assert database.get_page_translation(business_id, "es") is None


def test_pretranslate_skips_languages_already_cached(client, unique_email, monkeypatch):
    monkeypatch.setattr(translate_page, "detect_content_language", lambda text: "pt")
    calls = []

    def fake_translate(lang, tagline, about, price_list, faq):
        calls.append(lang)
        return _fake_translation(lang)

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    # Already cached from an earlier real visitor - pre-warming shouldn't pay for it again.
    database.upsert_page_translation(business_id, "es", "[es] Fresh cuts daily", "[es] x", "[es] y", "[es] z", json.dumps(dict(translate_page.PAGE_UI_STRINGS)))

    app_module._pretranslate_page_languages(business_id)

    assert "es" not in calls
    assert sorted(calls) == sorted(lang for lang in translate_page.DEFAULT_PAGE_LANGUAGES if lang != "es")


def test_pretranslate_does_nothing_for_an_unpublished_page(client, unique_email, monkeypatch):
    calls = []
    monkeypatch.setattr(translate_page, "translate_page_content", lambda lang, *a: calls.append(lang) or _fake_translation(lang))
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"tr-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, "Fresh cuts daily", "We opened in 2019.")

    app_module._pretranslate_page_languages(business_id)

    assert calls == []


def test_pretranslate_never_raises_when_a_language_fails_to_translate(client, unique_email, monkeypatch):
    def fake_translate(lang, tagline, about, price_list, faq):
        raise ValueError("model returned garbage")

    monkeypatch.setattr(translate_page, "translate_page_content", fake_translate)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)

    app_module._pretranslate_page_languages(business_id)  # must not raise

    for lang in translate_page.DEFAULT_PAGE_LANGUAGES:
        assert database.get_page_translation(business_id, lang) is None


def test_saving_the_page_via_the_web_form_triggers_a_background_pretranslate(client, unique_email, monkeypatch):
    from conftest import get_csrf_token, web_post

    calls = []
    monkeypatch.setattr(app_module, "_pretranslate_page_languages_in_background", lambda business_id: calls.append(business_id))
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    csrf_token = get_csrf_token(client, "/settings")
    web_post(client, "/profile/page", {
        "csrf_token": csrf_token, "slug": f"tr-{uuid.uuid4().hex[:8]}",
        "tagline": "Fresh cuts daily", "about": "We opened in 2019.",
    })
    assert calls == [business_id]


# --- translate_page.ui_translation_rejection_reason / translate_ui_strings /
# app._ui_strings_for_language - reproduces the reported bug: a business's public page
# chrome (nav labels/section headings, PAGE_UI_STRINGS) is translated and cached once per
# language, shared across every business, forever (see database.upsert_ui_translation). A
# response that left just one key - "heading_hours" ("Working hours") - copied through
# untranslated used to pass the old missing/empty-only check, get cached, and then serve
# that same untranslated "Working hours" heading to every business and visitor in that
# language forever, even though the page's nav ("nav_hours") and every other heading
# translated correctly. ---

def test_ui_translation_rejection_reason_none_for_a_fully_translated_result():
    ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    assert translate_page.ui_translation_rejection_reason(ui) is None


def test_ui_translation_rejection_reason_flags_a_missing_key():
    ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    ui["chat_send"] = ""
    assert "chat_send" in translate_page.ui_translation_rejection_reason(ui)


def test_ui_translation_rejection_reason_flags_a_long_key_left_untranslated():
    # Reproduces the exact reported bug: every key translated except "heading_hours",
    # which came back byte-for-byte identical to its English source.
    ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    ui["heading_hours"] = translate_page.PAGE_UI_STRINGS["heading_hours"]
    reason = translate_page.ui_translation_rejection_reason(ui)
    assert reason is not None
    assert "heading_hours" in reason


def test_ui_translation_rejection_reason_ignores_a_short_key_coincidentally_unchanged():
    # Short, generic words ("Home", "Send", ...) can legitimately come back identical in
    # another language - only long enough phrases are a reliable "not actually
    # translated" signal (see _UI_ECHO_MIN_LENGTH's own comment).
    ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    ui["nav_home"] = translate_page.PAGE_UI_STRINGS["nav_home"]
    assert translate_page.ui_translation_rejection_reason(ui) is None


def test_translate_ui_strings_retries_after_one_key_comes_back_untranslated(monkeypatch):
    bad_ui = dict(translate_page.PAGE_UI_STRINGS)
    bad_ui.update({key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items() if key != "heading_hours"})
    good_ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}

    def bad_response(**kwargs):
        content = json.dumps(bad_ui)
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

    def good_response(**kwargs):
        content = json.dumps(good_ui)
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

    attempts = [bad_response, good_response]
    monkeypatch.setattr(translate_page.client.chat.completions, "create", lambda **kwargs: attempts.pop(0)(**kwargs))

    result = translate_page.translate_ui_strings("sr")

    assert result == good_ui
    assert attempts == []  # both attempts were consumed - it really did retry


def test_ui_strings_for_language_reruns_the_model_when_the_cached_row_left_a_key_untranslated(monkeypatch):
    # A row cached before ui_translation_rejection_reason's echo check existed (or that
    # slipped past it) must not keep serving the same "Working hours" heading to every
    # business in Serbian forever - the same self-healing already applied to page-content
    # translations (see test_translate_endpoint_reruns_the_model_when_the_cached_row_echoed_
    # the_source above), extended to this separately-cached, business-agnostic chrome.
    stale_ui = dict(translate_page.PAGE_UI_STRINGS)
    stale_ui.update({key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items() if key != "heading_hours"})
    database.upsert_ui_translation("sr", json.dumps(stale_ui))

    fresh_ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    calls = []

    def fake_translate(lang):
        calls.append(lang)
        return fresh_ui

    monkeypatch.setattr(translate_page, "translate_ui_strings", fake_translate)

    result = app_module._ui_strings_for_language("sr")

    assert result == fresh_ui
    assert calls == ["sr"]
    # The regenerated result overwrote the stale row - a later call is served from cache
    # again, without a second model call.
    result_again = app_module._ui_strings_for_language("sr")
    assert result_again == fresh_ui
    assert calls == ["sr"]


def test_ui_strings_for_language_trusts_an_already_good_cached_row(monkeypatch):
    good_ui = {key: f"[sr] {value}" for key, value in translate_page.PAGE_UI_STRINGS.items()}
    database.upsert_ui_translation("sr", json.dumps(good_ui))

    def fake_translate(lang):
        raise AssertionError("should not re-translate an already-usable cached row")

    monkeypatch.setattr(translate_page, "translate_ui_strings", fake_translate)

    assert app_module._ui_strings_for_language("sr") == good_ui
