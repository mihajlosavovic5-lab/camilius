"""Route tests for POST /profile/generate-page - the AI-drafted starting point for the
public page. page_generator.py's own unit tests (test_page_generator.py) already cover
generate_page_draft()/format_*() directly; these exercise the route's own contract: it
re-renders settings.html with the draft pre-filled but MUST NOT write anything to the
database (see generate_business_page()'s docstring in app.py for why)."""
import json
import uuid

import database
from conftest import register_business_web, web_post, get_csrf_token
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
    "hours": {
        "monday": {"open": "09:00", "close": "18:00"},
        "tuesday": {"open": "09:00", "close": "18:00"},
        "wednesday": {"open": "09:00", "close": "18:00"},
        "thursday": {"open": "09:00", "close": "18:00"},
        "friday": {"open": "09:00", "close": "18:00"},
        "saturday": {"open": None, "close": None},
        "sunday": {"open": None, "close": None},
    },
    "services": [{"name": "Haircut", "price": "20 EUR"}],
    "faq": [{"question": "Do I need an appointment?", "answer": "Walk-ins welcome."}],
}


def test_generate_business_page_prefills_without_saving(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_DRAFT)),
    )
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    original_working_hours = database.business_by_id(business_id)[8]
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {
        "csrf_token": csrf_token, "description": "A barbershop open weekdays 9-6, haircuts 20 EUR.",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Fresh cuts, fair prices" in html
    assert "Family-run barbershop in the neighborhood since 2019." in html
    assert "Haircut: 20 EUR" in html
    # No FAQ assertion here - the "Tell us more about your business" field was dropped
    # from settings.html entirely (see "drop the business FAQ field"), so the drafted
    # FAQ pair has nowhere left to render on this page even though generate_page_draft()
    # still returns one (format_faq(draft["faq"]) is computed but unused downstream).

    # Nothing was persisted - the draft only appears in this response's rendered form.
    assert database.business_by_id(business_id)[8] == original_working_hours
    assert database.get_business_page(business_id) is None


def test_generate_business_page_requires_description(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {"csrf_token": csrf_token, "description": "   "})
    assert response.status_code == 200
    assert "Describe your business first" in response.get_data(as_text=True)


def test_generate_business_page_shows_error_on_bad_model_output(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse("not json"),
    )
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {
        "csrf_token": csrf_token, "description": "A barbershop.",
    })
    assert response.status_code == 200
    assert "generate a draft from that" in response.get_data(as_text=True)


def test_generate_business_page_requires_login(client):
    csrf_token = get_csrf_token(client, "/login")
    response = web_post(client, "/profile/generate-page", {"csrf_token": csrf_token, "description": "A barbershop."})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_generate_business_page_prefills_suggested_slug_when_none_saved(client, unique_username, unique_email, monkeypatch):
    suggested_slug = f"the-blue-scissors-{uuid.uuid4().hex[:8]}"
    draft = dict(VALID_DRAFT, slug=suggested_slug)
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(draft)),
    )
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {
        "csrf_token": csrf_token, "description": "A barbershop open weekdays 9-6, haircuts 20 EUR.",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'value="{suggested_slug}"' in html


def test_generate_business_page_keeps_already_saved_slug(client, unique_username, unique_email, monkeypatch):
    existing_slug = f"already-chosen-{uuid.uuid4().hex[:8]}"
    draft = dict(VALID_DRAFT, slug=f"a-totally-different-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(draft)),
    )
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    database.upsert_business_page(business_id, existing_slug, False, "Old tagline", "Old about")
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {
        "csrf_token": csrf_token, "description": "A barbershop open weekdays 9-6, haircuts 20 EUR.",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'value="{existing_slug}"' in html
    assert draft["slug"] not in html


def test_generate_business_page_falls_back_to_business_name_slug_when_ai_slug_taken(client, unique_username, unique_email, monkeypatch):
    taken_slug = f"taken-{uuid.uuid4().hex[:8]}"
    register_business_web(client, "slug-owner-" + unique_username, "owner-" + unique_email)
    owner_id = database.business_by_username("slug-owner-" + unique_username)[0]
    database.upsert_business_page(owner_id, taken_slug, True, "T", "A")

    draft = dict(VALID_DRAFT, slug=taken_slug)
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(draft)),
    )
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/profile")

    response = web_post(client, "/profile/generate-page", {
        "csrf_token": csrf_token, "description": "A barbershop open weekdays 9-6, haircuts 20 EUR.",
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'value="{taken_slug}"' not in html
