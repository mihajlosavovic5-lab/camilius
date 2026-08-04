"""Faza 13: the mandatory "build your website" wizard a business is sent through right
after email verification (see onboarding_required/onboarding_website_* in app.py) -
describe -> pick a design -> pick a web address -> review & publish. Mocks the AI draft
call the same way test_generate_page_route.py does (this module reuses the exact same
page_generator.py pipeline), since these are route/flow tests, not a second copy of
page_generator.py's own unit tests."""
import json
import uuid

import database
import themes
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


def _mock_ai_draft(monkeypatch):
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_DRAFT)),
    )


def _run_describe_step(client, monkeypatch):
    _mock_ai_draft(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")
    return web_post(client, "/onboarding/website", {
        "csrf_token": csrf_token, "description": "A barbershop open weekdays 9-6, haircuts 20 EUR.",
    })


def test_wizard_happy_path_publishes_a_page_and_completes_onboarding(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]

    # Step 1: describe -> AI draft, redirects to the design step.
    response = _run_describe_step(client, monkeypatch)
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/website/design"

    # Step 2: pick a design, redirects to the address step.
    csrf_token = get_csrf_token(client, "/onboarding/website/design")
    response = web_post(client, "/onboarding/website/design", {"csrf_token": csrf_token, "theme": "bold"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/website/address"

    # Step 3: pick a web address, redirects to review.
    slug = f"salon-{uuid.uuid4().hex[:8]}"
    csrf_token = get_csrf_token(client, "/onboarding/website/address")
    response = web_post(client, "/onboarding/website/address", {"csrf_token": csrf_token, "slug": slug})
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/website/review"

    # Step 4: review shows the chosen slug, then publishing completes onboarding.
    review_html = client.get("/onboarding/website/review").get_data(as_text=True)
    assert slug in review_html

    csrf_token = get_csrf_token(client, "/onboarding/website/review")
    response = web_post(client, "/onboarding/website/review", {"csrf_token": csrf_token})
    assert response.status_code == 302
    assert response.headers["Location"] == "/?onboarding_done=1"

    page = database.get_business_page(business_id)
    assert page[2] == slug  # slug
    assert page[3] is True  # published
    assert page[4] == "Fresh cuts, fair prices"  # tagline
    assert page[9] == "bold"  # theme

    business = database.business_by_id(business_id)
    assert "Haircut: 20 EUR" in business[9]  # price_list, AI-drafted

    assert database.onboarding_completed(business_id) is True
    assert client.get("/").status_code == 200


def test_onboarding_describe_requires_a_description(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = web_post(client, "/onboarding/website", {"csrf_token": csrf_token, "description": "   "})
    assert response.status_code == 200
    assert "Describe your business first" in response.get_data(as_text=True)


def test_onboarding_design_rejects_unknown_theme(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _run_describe_step(client, monkeypatch)

    csrf_token = get_csrf_token(client, "/onboarding/website/design")
    response = web_post(client, "/onboarding/website/design", {"csrf_token": csrf_token, "theme": "not-a-real-theme"})
    assert response.status_code == 200
    assert "Please choose a design" in response.get_data(as_text=True)


def test_onboarding_address_rejects_invalid_and_taken_slugs(client, unique_username, unique_email, monkeypatch):
    # Another business already publishing under this slug.
    other_username = f"{unique_username}-other"
    other_email = f"other-{unique_email}"
    register_business_web(client, other_username, other_email)
    other_id = database.business_by_username(other_username)[0]
    taken_slug = f"taken-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(other_id, taken_slug, True, "Tag", "About")
    client.get("/logout")

    register_business_web(client, unique_username, unique_email)
    _run_describe_step(client, monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website/design")
    web_post(client, "/onboarding/website/design", {"csrf_token": csrf_token, "theme": "minimal"})

    csrf_token = get_csrf_token(client, "/onboarding/website/address")
    response = web_post(client, "/onboarding/website/address", {"csrf_token": csrf_token, "slug": "Not A Valid Slug!"})
    assert response.status_code == 200
    assert "only lowercase letters" in response.get_data(as_text=True)

    csrf_token = get_csrf_token(client, "/onboarding/website/address")
    response = web_post(client, "/onboarding/website/address", {"csrf_token": csrf_token, "slug": taken_slug})
    assert response.status_code == 200
    assert "already taken" in response.get_data(as_text=True)


def test_onboarding_steps_bounce_back_when_the_draft_is_missing(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)

    # Jumping straight to a later step without the earlier one's session state redirects
    # back to the start, rather than 500ing on a missing session key.
    for path in ("/onboarding/website/design", "/onboarding/website/address", "/onboarding/website/review"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"] == "/onboarding/website"

    assert client.get("/onboarding/website/preview").status_code == 404


def test_onboarding_preview_renders_the_chosen_theme_without_chat(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _run_describe_step(client, monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website/design")
    web_post(client, "/onboarding/website/design", {"csrf_token": csrf_token, "theme": "luxury"})

    response = client.get("/onboarding/website/preview")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert themes.THEMES["luxury"]["base"]["primary"] in html
    assert "Fresh cuts, fair prices" in html
    assert 'id="chat-fab"' not in html  # preview mode hides the (not-yet-real) chat widget

    # ?theme= overrides the draft's own saved theme, for the design step's per-card preview.
    response = client.get("/onboarding/website/preview?theme=nature")
    html = response.get_data(as_text=True)
    assert themes.THEMES["nature"]["base"]["primary"] in html


def test_onboarding_wizard_requires_login(client):
    response = client.get("/onboarding/website")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
