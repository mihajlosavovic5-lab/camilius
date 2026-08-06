"""Tests for the new "/" site overview (Faza 15) - the post-login landing page that
replaced the raw conversation inbox there (see app.py's home()/conversations_home()
split, and test_home_dashboard_gate.py for the onboarding-gate side of this). Covers what
that page actually shows: the business's own website card and an upcoming-appointments
summary, on top of the redirect/reachability behavior already covered elsewhere."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import database
from conftest import register_business_web

TZ = ZoneInfo("Europe/Belgrade")


def _future_slot(hours_from_now=48):
    start = datetime.now(TZ).replace(minute=0, second=0, microsecond=0) + timedelta(hours=hours_from_now)
    return start, start + timedelta(minutes=30)


def test_site_overview_shows_no_website_cta_when_nothing_saved(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])

    html = client.get("/").get_data(as_text=True)
    assert "haven&#39;t set up your website" in html or "haven't set up your website" in html


def test_site_overview_shows_tagline_and_live_link_for_a_published_page(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])
    slug = f"cool-salon-{uuid.uuid4().hex[:8]}"
    database.upsert_business_page(business[0], slug, True, "Fresh cuts, fair prices", "A great salon.", None, "warm")

    html = client.get("/").get_data(as_text=True)
    assert "Fresh cuts, fair prices" in html
    assert f"/p/{slug}" in html


def test_site_overview_lists_upcoming_appointments(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])
    start, end = _future_slot()
    database.create_appointment(business[0], "Ana", start, end, service="Haircut")

    html = client.get("/").get_data(as_text=True)
    assert "Ana" in html
    assert "Haircut" in html


def test_site_overview_appointments_stat_reflects_count(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])
    start, end = _future_slot()
    database.create_appointment(business[0], "Ana", start, end)
    later_start, later_end = _future_slot(hours_from_now=72)
    database.create_appointment(business[0], "Bob", later_start, later_end)

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Ana" in html and "Bob" in html


def test_nav_shows_my_website_link(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business = database.business_by_username(unique_username)
    database.mark_onboarding_completed(business[0])

    html = client.get("/").get_data(as_text=True)
    assert 'href="/"' in html
