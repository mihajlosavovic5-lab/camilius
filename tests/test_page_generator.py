"""Tests for page_generator.py - the format_* functions are pure and tested directly;
generate_page_draft() is tested with the OpenAI client monkeypatched (same "never make a
real API call in tests" rule the rest of the suite follows for generate_reply.py)."""
import json

import pytest

import booking
import page_generator


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


VALID_DRAFT = {
    "tagline": "Fresh cuts, fair prices",
    "about": "Family-run barbershop in the neighborhood since 2019.",
    "slug": "fresh-cuts-barbershop",
    "hours": {
        "monday": {"open": "09:00", "close": "18:00"},
        "tuesday": {"open": "09:00", "close": "18:00"},
        "wednesday": {"open": "09:00", "close": "18:00"},
        "thursday": {"open": "09:00", "close": "18:00"},
        "friday": {"open": "09:00", "close": "18:00"},
        "saturday": {"open": None, "close": None},
        "sunday": {"open": None, "close": None},
    },
    "services": [{"name": "Haircut", "price": "20 EUR"}, {"name": "Beard trim", "price": ""}],
    "faq": [{"question": "Do I need an appointment?", "answer": "Walk-ins welcome, but booking ahead is faster."}],
}


# --- generate_page_draft ---

def test_generate_page_draft_returns_parsed_json(monkeypatch):
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_DRAFT)),
    )
    result = page_generator.generate_page_draft("A barbershop open weekdays 9-6, haircuts 20 EUR.")
    assert result == VALID_DRAFT


def test_generate_page_draft_passes_description_and_schema(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("A barbershop.")
    assert captured["messages"][-1] == {"role": "user", "content": "A barbershop."}
    assert captured["response_format"]["json_schema"]["schema"] == page_generator.PAGE_SCHEMA
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert "slug" in page_generator.PAGE_SCHEMA["required"]


def test_generate_page_draft_rejects_empty_description():
    with pytest.raises(ValueError):
        page_generator.generate_page_draft("")
    with pytest.raises(ValueError):
        page_generator.generate_page_draft("   ")


def test_generate_page_draft_raises_on_empty_content(monkeypatch):
    monkeypatch.setattr(page_generator.client.chat.completions, "create", lambda **kwargs: _FakeResponse(None))
    with pytest.raises(ValueError):
        page_generator.generate_page_draft("A barbershop.")


def test_generate_page_draft_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(page_generator.client.chat.completions, "create", lambda **kwargs: _FakeResponse("not json"))
    with pytest.raises(ValueError):
        page_generator.generate_page_draft("A barbershop.")


def test_generate_page_draft_truncates_long_description(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("x" * 5000)
    sent = captured["messages"][-1]["content"]
    assert len(sent) == page_generator.MAX_DESCRIPTION_LENGTH


# --- generate_page_draft: document_text ---

def test_generate_page_draft_without_document_text_matches_old_behavior(monkeypatch):
    """The text-only flow (no document uploaded) must keep working exactly as before -
    omitting document_text (or passing None) sends the same two messages as always."""
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("A barbershop.", document_text=None)
    assert captured["messages"] == [
        {"role": "system", "content": page_generator.SYSTEM_PROMPT},
        {"role": "user", "content": "A barbershop."},
    ]


def test_generate_page_draft_appends_document_text_as_extra_message(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("A barbershop.", document_text="Haircut: 20 EUR\nBeard trim: 10 EUR")
    assert len(captured["messages"]) == 3
    assert captured["messages"][1] == {"role": "user", "content": "A barbershop."}
    document_message = captured["messages"][2]
    assert document_message["role"] == "user"
    assert "Haircut: 20 EUR\nBeard trim: 10 EUR" in document_message["content"]
    # The model must be told to treat this as fact, not something it's free to rewrite.
    assert "ground truth" in document_message["content"].lower()


def test_generate_page_draft_ignores_blank_document_text(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("A barbershop.", document_text="   ")
    assert len(captured["messages"]) == 2


def test_generate_page_draft_truncates_long_document_text(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(json.dumps(VALID_DRAFT))

    monkeypatch.setattr(page_generator.client.chat.completions, "create", fake_create)
    page_generator.generate_page_draft("A barbershop.", document_text="y" * 20000)
    document_message = captured["messages"][2]["content"]
    # The document text itself (not the surrounding instruction) is capped.
    assert document_message.endswith("y" * page_generator.MAX_DOCUMENT_TEXT_LENGTH)
    assert not document_message.endswith("y" * (page_generator.MAX_DOCUMENT_TEXT_LENGTH + 1))


# --- format_working_hours ---

def test_format_working_hours_matches_picker_wire_format():
    text = page_generator.format_working_hours(VALID_DRAFT["hours"])
    assert json.loads(text) == {
        "monday": {"open": "09:00", "close": "18:00"},
        "tuesday": {"open": "09:00", "close": "18:00"},
        "wednesday": {"open": "09:00", "close": "18:00"},
        "thursday": {"open": "09:00", "close": "18:00"},
        "friday": {"open": "09:00", "close": "18:00"},
        "saturday": None,
        "sunday": None,
    }


def test_format_working_hours_round_trips_through_booking_parser():
    """The whole point of matching the picker's wire format - booking.py's own parser
    must read the generated text back correctly, with no special-casing for
    AI-generated vs. picker-typed text."""
    text = page_generator.format_working_hours(VALID_DRAFT["hours"])
    parsed = booking.parse_working_hours(text)
    assert parsed[0] == (booking.dtime(9, 0), booking.dtime(18, 0))  # Monday
    assert parsed[5] is None  # Saturday
    assert parsed[6] is None  # Sunday


def test_format_working_hours_handles_missing_or_partial_day():
    text = page_generator.format_working_hours({"monday": {"open": "09:00", "close": None}})
    parsed = json.loads(text)
    assert parsed["monday"] is None  # incomplete pair treated as closed
    assert parsed["tuesday"] is None  # day absent entirely from the dict


# --- format_price_list ---

def test_format_price_list_includes_price_when_given():
    text = page_generator.format_price_list([{"name": "Haircut", "price": "20 EUR"}])
    assert text == "Haircut: 20 EUR"


def test_format_price_list_omits_price_when_blank():
    text = page_generator.format_price_list([{"name": "Beard trim", "price": ""}])
    assert text == "Beard trim"


def test_format_price_list_skips_entries_with_no_name():
    text = page_generator.format_price_list([{"name": "", "price": "10 EUR"}, {"name": "Haircut", "price": ""}])
    assert text == "Haircut"


def test_format_price_list_multiple_services_one_per_line():
    text = page_generator.format_price_list(VALID_DRAFT["services"])
    assert text == "Haircut: 20 EUR\nBeard trim"


# --- format_faq ---

def test_format_faq_joins_question_and_answer():
    text = page_generator.format_faq(VALID_DRAFT["faq"])
    assert text == "Do I need an appointment?\nWalk-ins welcome, but booking ahead is faster."


def test_format_faq_skips_incomplete_pairs():
    text = page_generator.format_faq([{"question": "Q?", "answer": ""}, {"question": "", "answer": "A."}])
    assert text == ""


def test_format_faq_multiple_pairs_separated_by_blank_line():
    faq = [
        {"question": "Q1?", "answer": "A1."},
        {"question": "Q2?", "answer": "A2."},
    ]
    text = page_generator.format_faq(faq)
    assert text == "Q1?\nA1.\n\nQ2?\nA2."
