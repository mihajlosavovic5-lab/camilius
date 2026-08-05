"""Tests for ai_theme.py - the AI-generated per-business theme options used by the
onboarding wizard's design step and Settings' "Suggest AI designs" button (see
app.py's onboarding_website_design()/suggest_business_themes()). Same "monkeypatch the
OpenAI client, never make a real call" rule as test_page_generator.py."""
import json

import ai_theme
import themes


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _color_set(primary="#d69410", on_primary="#201a10", gold="#ffd83d", background="#fdfcfa",
               surface="#ffffff", text="#2b2013", text_muted="#6b5d47", border="#eee3d0"):
    return {
        "primary": primary, "on_primary": on_primary, "gold": gold, "background": background,
        "surface": surface, "text": text, "text_muted": text_muted, "border": border,
    }


def _theme_option(label="Calm Clinic", fixed_dark=False):
    return {
        "label": label, "blurb": "A calm, professional look.", "radius": "8px",
        "heading_font": None, "fixed_dark": fixed_dark,
        "base": _color_set(), "dark": _color_set(background="#201a10", text="#f5efe2"),
    }


VALID_THEMES_RESPONSE = {"themes": [_theme_option("Calm Clinic"), _theme_option("Warm Reception"), _theme_option("Bold Studio")]}


def test_generate_theme_options_returns_validated_list(monkeypatch):
    monkeypatch.setattr(
        ai_theme.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_THEMES_RESPONSE)),
    )
    options = ai_theme.generate_theme_options("A dentist's office, calm and professional.")
    assert len(options) == 3
    labels = [o["label"] for o in options]
    assert labels == ["Calm Clinic", "Warm Reception", "Bold Studio"]
    # Shaped exactly like a themes.THEMES[...] entry so templates need no special-casing.
    for option in options:
        assert set(option.keys()) == {"label", "blurb", "swatch", "radius", "heading_font", "fixed_dark", "base", "dark"}
        assert len(option["swatch"]) == 3


def test_generate_theme_options_empty_description_returns_empty_list():
    assert ai_theme.generate_theme_options("") == []
    assert ai_theme.generate_theme_options("   ") == []


def test_generate_theme_options_returns_empty_list_on_model_error(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", raise_error)
    assert ai_theme.generate_theme_options("A bakery.") == []


def test_generate_theme_options_returns_empty_list_on_invalid_json(monkeypatch):
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", lambda **kwargs: _FakeResponse("not json"))
    assert ai_theme.generate_theme_options("A bakery.") == []


def test_generate_theme_options_drops_options_missing_required_colors(monkeypatch):
    broken = _theme_option("Broken")
    del broken["base"]["primary"]
    response = {"themes": [broken, _theme_option("Fine")]}
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", lambda **kwargs: _FakeResponse(json.dumps(response)))
    options = ai_theme.generate_theme_options("A shop.")
    assert len(options) == 1
    assert options[0]["label"] == "Fine"


def test_generate_theme_options_drops_options_with_bad_hex(monkeypatch):
    broken = _theme_option("Broken")
    broken["base"]["primary"] = "not-a-color"
    response = {"themes": [broken]}
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", lambda **kwargs: _FakeResponse(json.dumps(response)))
    assert ai_theme.generate_theme_options("A shop.") == []


def test_generate_theme_options_fixed_dark_option_has_no_dark_block(monkeypatch):
    response = {"themes": [_theme_option("Bold Studio", fixed_dark=True)]}
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", lambda **kwargs: _FakeResponse(json.dumps(response)))
    options = ai_theme.generate_theme_options("A gym.")
    assert options[0]["fixed_dark"] is True
    assert options[0]["dark"] is None


def test_generate_theme_options_clamps_low_contrast_text(monkeypatch):
    # text nearly identical to background - contrast ratio well under the 3.0 floor.
    low_contrast = _theme_option("Low Contrast")
    low_contrast["base"] = _color_set(background="#ffffff", text="#fefefe")
    response = {"themes": [low_contrast]}
    monkeypatch.setattr(ai_theme.client.chat.completions, "create", lambda **kwargs: _FakeResponse(json.dumps(response)))
    options = ai_theme.generate_theme_options("A shop.")
    assert options[0]["base"]["text"] != "#fefefe"
    assert ai_theme._contrast_ratio(options[0]["base"]["text"], options[0]["base"]["background"]) >= 3.0


def test_generate_theme_options_truncates_long_description(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_THEMES_RESPONSE))

    monkeypatch.setattr(ai_theme.client.chat.completions, "create", fake_create)
    ai_theme.generate_theme_options("x" * 5000)
    sent = captured["messages"][1]["content"]
    assert len(sent) == ai_theme.MAX_DESCRIPTION_LENGTH


def test_generate_theme_options_appends_document_text(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_THEMES_RESPONSE))

    monkeypatch.setattr(ai_theme.client.chat.completions, "create", fake_create)
    ai_theme.generate_theme_options("A dentist.", document_text="Cleaning: 40 EUR")
    assert len(captured["messages"]) == 3
    assert "Cleaning: 40 EUR" in captured["messages"][2]["content"]


# --- themes.resolve_theme ---

def test_resolve_theme_returns_preset_for_classic_id():
    assert themes.resolve_theme("minimal", None) == themes.THEMES["minimal"]


def test_resolve_theme_falls_back_to_default_for_unknown_id():
    assert themes.resolve_theme("nonexistent", None) == themes.THEMES[themes.DEFAULT_THEME_ID]


def test_resolve_theme_returns_custom_json_when_theme_is_custom():
    custom = _theme_option("My Custom Theme")
    resolved = themes.resolve_theme(themes.CUSTOM_THEME_ID, json.dumps(custom))
    assert resolved["label"] == "My Custom Theme"


def test_resolve_theme_falls_back_when_custom_json_is_invalid():
    resolved = themes.resolve_theme(themes.CUSTOM_THEME_ID, "not json")
    assert resolved == themes.THEMES[themes.DEFAULT_THEME_ID]


def test_resolve_theme_falls_back_when_custom_selected_but_no_json_saved():
    resolved = themes.resolve_theme(themes.CUSTOM_THEME_ID, None)
    assert resolved == themes.THEMES[themes.DEFAULT_THEME_ID]
