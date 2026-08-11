"""Tests for the public page's visible "Book an appointment" section and its backing
GET /p/<slug>/availability endpoint - see app.py's public_page()/public_page_availability
and templates/public_page.html's #book-section. Booking must only ever be advertised
where the AI chat's own booking tool would actually accept it (has_pro_access/
BETA_FREE_MODE, same gate as ai_replies.handle_incoming_message), and only for a business
with at least one open day - see test_schedule_routes.py for the same Pro-gating pattern
applied to the Schedule page itself.
"""
import uuid

import app as app_module
import database
from conftest import register_business_web


def _publish_page(client, unique_email):
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"book-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, True, "Fresh cuts daily", None)
    return business_id, slug


def test_public_page_shows_booking_section_during_trial(client, unique_email):
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert "Book an appointment" in html
    assert f"/p/{slug}/availability" in html


def test_public_page_hides_booking_section_for_starter_plan(client, unique_email, monkeypatch):
    # This test is about real billing enforcement (Starter plan lacking Pro access),
    # which BETA_FREE_MODE deliberately bypasses for everyone - pin it off here so the
    # test still exercises the has_pro_access gate it's named for. See app.py's own
    # BETA_FREE_MODE comment.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert "Book an appointment" not in html
    assert 'id="book-cta"' not in html


def test_public_page_hides_booking_section_when_no_open_days(client, unique_email):
    register_business_web(client, unique_email, working_hours=(
        "Monday: closed\nTuesday: closed\nWednesday: closed\n"
        "Thursday: closed\nFriday: closed\nSaturday: closed\nSunday: closed"
    ))
    _, slug = _publish_page(client, unique_email)

    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert "Book an appointment" not in html


def test_availability_endpoint_returns_slots_within_working_hours(client, unique_email):
    register_business_web(client, unique_email)
    _, slug = _publish_page(client, unique_email)

    response = client.get(f"/p/{slug}/availability")
    assert response.status_code == 200
    data = response.get_json()
    assert "days" in data
    assert len(data["days"]) > 0
    for day in data["days"]:
        assert set(day.keys()) == {"date", "label", "slots"}
        assert len(day["slots"]) > 0
        for slot in day["slots"]:
            assert "09:00" <= slot < "18:00"


def test_availability_endpoint_404s_for_starter_plan(client, unique_email, monkeypatch):
    # Same reasoning as test_public_page_hides_booking_section_for_starter_plan above -
    # pin real billing enforcement on so this still tests the Starter-plan gate.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    register_business_web(client, unique_email)
    business_id, slug = _publish_page(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.get(f"/p/{slug}/availability")
    assert response.status_code == 404


def test_availability_endpoint_404s_for_unknown_slug(client):
    response = client.get(f"/p/does-not-exist-{uuid.uuid4().hex[:8]}/availability")
    assert response.status_code == 404


def test_availability_endpoint_404s_when_not_published_for_a_stranger(client, unique_email):
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)

    # A visitor with no session on this business at all - _page_visible_to_request's
    # owner carve-out (see the next test) must not widen access beyond that.
    with client.session_transaction() as session:
        session.pop("business_id", None)

    response = client.get(f"/p/{slug}/availability")
    assert response.status_code == 404


def test_availability_endpoint_shows_real_times_for_owners_own_unpublished_preview(client, unique_email):
    """_page_visible_to_request's carve-out (see app.py) - read-only and side-effect-free,
    so the business's own site_preview() ("View page") can show real open times instead
    of a permanent error while the page is still a draft, matching the live layout."""
    register_business_web(client, unique_email)
    business_id = database.business_by_contact_email(unique_email)[0]
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business_id, slug, False, None, None)

    response = client.get(f"/p/{slug}/availability")
    assert response.status_code == 200
    assert "days" in response.get_json()
