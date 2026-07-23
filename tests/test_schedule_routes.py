"""HTTP-level tests for the Schedule page's Faza 10 feature gating and the trial-expiry
redirect - uses the web session+CSRF flow (register_business_web/get_csrf_token/web_post
in conftest.py), not the mobile /api/* Bearer-token routes the rest of the suite uses,
since Schedule only exists on the website."""
import re

import database
from conftest import get_csrf_token, register_business_web, web_post


def test_schedule_requires_login(client):
    response = client.get("/schedule")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_schedule_unlocked_during_trial(client, unique_username, unique_email):
    reg = register_business_web(client, unique_username, unique_email)
    assert reg.status_code == 302  # -> /connect-instagram, but the session is now logged in

    response = client.get("/schedule")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Schedule is a Pro plan feature" not in body
    assert "schedule-timeline" in body or "Closed on this day" in body


def test_schedule_locked_for_starter_plan(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.get("/schedule")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Schedule is a Pro plan feature" in body
    assert "schedule-timeline" not in body


def test_schedule_locked_starter_cannot_bypass_via_post(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    # CSRF token pulled from /app-settings, not the now-locked /schedule page - the
    # locked page has no form on it at all (see schedule.html), so no token to extract;
    # CSRF tokens are session-scoped, so any other page's token works here regardless.
    csrf_token = get_csrf_token(client, "/app-settings")
    business_id = database.business_by_username(unique_username)[0]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": "2026-07-27", "time": "10:00", "customer_name": "Bypass Attempt",
    })
    assert response.status_code == 403


def test_book_and_cancel_appointment_via_web(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    # 2026-07-27 is a Monday - the test business's working_hours (see
    # register_business_web) is open 09:00-18:00 that day.
    create = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": "2026-07-27", "time": "10:00",
        "customer_name": "Test Customer", "service": "Haircut",
    })
    assert create.status_code == 302

    day_view = client.get("/schedule?date=2026-07-27")
    body = day_view.get_data(as_text=True)
    assert "Test Customer" in body
    assert "Haircut" in body

    # Extract the appointment id from the rendered cancel form's action URL.
    match = re.search(r"/schedule/appointments/(\d+)/cancel", body)
    assert match is not None
    appointment_id = match.group(1)

    cancel_csrf = get_csrf_token(client, "/schedule?date=2026-07-27")
    cancel = web_post(client, f"/schedule/appointments/{appointment_id}/cancel", {
        "csrf_token": cancel_csrf, "date": "2026-07-27", "view": "day",
    })
    assert cancel.status_code == 302

    after_cancel = client.get("/schedule?date=2026-07-27").get_data(as_text=True)
    assert "Test Customer" not in after_cancel


def test_double_booking_the_same_slot_is_rejected(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)

    csrf_1 = get_csrf_token(client, "/schedule")
    first = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_1, "date": "2026-07-27", "time": "11:00", "customer_name": "First Customer",
    })
    assert first.status_code == 302

    csrf_2 = get_csrf_token(client, "/schedule")
    second = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_2, "date": "2026-07-27", "time": "11:00", "customer_name": "Second Customer",
    })
    assert second.status_code == 302
    assert "book_error=1" in second.headers["Location"]

    body = client.get("/schedule?date=2026-07-27").get_data(as_text=True)
    assert "Second Customer" not in body
    assert "First Customer" in body


def test_home_redirects_to_subscription_once_access_lapses(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]

    # Still trialing - the core pages must NOT redirect away.
    assert client.get("/schedule").status_code == 200

    # Force the subscription inactive (simulates a lapsed/canceled account without
    # waiting for the real 14-day trial clock) - subscription_required should now kick in.
    database.link_subscription(business_id, "cust", "sub", "canceled")

    schedule_response = client.get("/schedule")
    assert schedule_response.status_code == 302
    assert "/subscription" in schedule_response.headers["Location"]

    home_response = client.get("/", follow_redirects=True)
    assert home_response.status_code == 200
    assert "Your 14-day free trial has ended" in home_response.get_data(as_text=True)
