"""Tests for website_content_tools.py - the "Draft this with AI" chat's write-access
tools (Faza 19). These call the tool implementations directly (no OpenAI involved, same
"mock the AI call, test the business logic underneath it directly" split the booking
tools use - see test_booking_logic.py) against the real test database, so a read-modify-
write bug (e.g. one field's update accidentally blanking a sibling field) shows up here
rather than only in a live chat."""
from datetime import time

import booking
import database
import website_content_tools as tools
from conftest import register_business


def _register(client, unique_email, **overrides):
    response = register_business(client, unique_email, **overrides)
    return response.get_json()["business_id"]


def test_update_price_list_preserves_working_hours_and_faq(client, unique_email):
    business_id = _register(client, unique_email, price_list="Old: 50", faq="Old FAQ")

    result = tools.update_price_list(business_id, "Haircut: 1500 din")

    assert result["status"] == "ok"
    business = database.business_by_id(business_id)
    assert business[8] == "Haircut: 1500 din"
    assert business[7] == "Mon-Fri 09:00-18:00"  # working_hours untouched
    assert business[9] == "Old FAQ"  # faq untouched


def test_update_faq_preserves_price_list(client, unique_email):
    business_id = _register(client, unique_email, price_list="Cut: 100")

    result = tools.update_faq(business_id, "Do you take walk-ins?\nYes, always.")

    assert result["status"] == "ok"
    business = database.business_by_id(business_id)
    assert business[9] == "Do you take walk-ins?\nYes, always."
    assert business[8] == "Cut: 100"


def test_update_working_hours_only_changes_the_given_day(client, unique_email):
    business_id = _register(client, unique_email)
    # register_business's own default working_hours ("Mon-Fri 09:00-18:00", a range
    # shorthand) isn't a shape parse_working_hours understands at all (it wants one
    # "monday: 09:00-18:00"-style line per day, or the JSON wire format) - so it parses
    # to every day closed. Seed a real starting state through the tool itself first,
    # same as how a business's hours actually get into the JSON format for real.
    tools.update_working_hours(business_id, [
        {"day": "monday", "open": "09:00", "close": "18:00"},
        {"day": "tuesday", "open": "09:00", "close": "18:00"},
    ])

    result = tools.update_working_hours(business_id, [{"day": "saturday", "open": "10:00", "close": "14:00"}])

    assert result["status"] == "ok"
    business = database.business_by_id(business_id)
    hours = booking.parse_working_hours(business[7])
    assert hours[0] == (time(9, 0), time(18, 0))  # Monday untouched
    assert hours[1] == (time(9, 0), time(18, 0))  # Tuesday untouched
    assert hours[5] == (time(10, 0), time(14, 0))  # Saturday now open
    assert hours[6] is None  # Sunday still closed


def test_update_working_hours_can_close_a_day(client, unique_email):
    business_id = _register(client, unique_email)
    tools.update_working_hours(business_id, [
        {"day": "monday", "open": "09:00", "close": "18:00"},
        {"day": "tuesday", "open": "09:00", "close": "18:00"},
    ])

    result = tools.update_working_hours(business_id, [{"day": "monday", "closed": True}])

    assert result["status"] == "ok"
    business = database.business_by_id(business_id)
    hours = booking.parse_working_hours(business[7])
    assert hours[0] is None  # Monday now closed
    assert hours[1] == (time(9, 0), time(18, 0))  # Tuesday untouched


def test_update_social_links_partial_update_preserves_others(client, unique_email):
    business_id = _register(client, unique_email)

    first = tools.update_social_links(business_id, instagram="mybakery")
    assert first["status"] == "ok"
    business = database.business_by_id(business_id)
    assert business[24] == "https://instagram.com/mybakery"
    assert business[25] is None

    second = tools.update_social_links(business_id, facebook="myBakeryPage")
    assert second["status"] == "ok"
    business = database.business_by_id(business_id)
    assert business[24] == "https://instagram.com/mybakery"  # untouched by the second call
    assert business[25] == "https://facebook.com/myBakeryPage"


def test_update_website_content_requires_a_web_address_first(client, unique_email):
    business_id = _register(client, unique_email)

    result = tools.update_website_content(business_id, tagline="Fresh bread daily")

    assert result["status"] == "error"
    assert database.get_business_page(business_id) is None


def test_update_website_content_preserves_slug_and_about_when_only_tagline_given(client, unique_email):
    business_id = _register(client, unique_email)
    slug = f"my-bakery-{business_id}"
    database.upsert_business_page(business_id, slug, True, "Old tagline", "Old about")

    result = tools.update_website_content(business_id, tagline="New tagline")

    assert result["status"] == "ok"
    page = database.get_business_page(business_id)
    assert page[2] == slug  # slug untouched
    assert page[3] is True  # published untouched
    assert page[4] == "New tagline"
    assert page[5] == "Old about"  # about untouched


def test_build_tool_impls_wires_every_website_tool(client, unique_email):
    business_id = _register(client, unique_email)
    impls = tools.build_tool_impls(business_id)
    assert set(impls.keys()) == {
        "update_website_content", "update_working_hours", "update_price_list",
        "update_faq", "update_social_links",
    }
    result = impls["update_price_list"](price_list="Wash: 20")
    assert result["status"] == "ok"
    assert database.business_by_id(business_id)[8] == "Wash: 20"
