"""HTTP-level tests for the Schedule page's Faza 10 feature gating and the trial-expiry
redirect - uses the web session+CSRF flow (register_business_web/get_csrf_token/web_post
in conftest.py), not the mobile /api/* Bearer-token routes the rest of the suite uses,
since Schedule only exists on the website."""
import re

import app as app_module
import database
from conftest import get_csrf_token, register_business_web, web_post


def test_schedule_requires_login(client):
    response = client.get("/schedule")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_schedule_unlocked_during_trial(client, unique_username, unique_email):
    reg = register_business_web(client, unique_username, unique_email)
    assert reg.status_code == 302  # -> /verify-pending, but the session is now logged in

    response = client.get("/schedule")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Schedule is a Pro plan feature" not in body
    assert "schedule-timeline" in body or "Closed on this day" in body


def test_schedule_locked_for_starter_plan(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.get("/schedule")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Schedule is a Pro plan feature" in body
    assert "schedule-timeline" not in body


def test_schedule_locked_starter_cannot_bypass_via_post(client, unique_username, unique_email, monkeypatch, future_date):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    register_business_web(client, unique_username, unique_email)
    # CSRF token pulled from /settings, not the now-locked /schedule page - the
    # locked page has no form on it at all (see schedule.html), so no token to extract;
    # CSRF tokens are session-scoped, so any other page's token works here regardless.
    csrf_token = get_csrf_token(client, "/settings")
    business_id = database.business_by_username(unique_username)[0]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": future_date, "time": "10:00", "customer_name": "Bypass Attempt",
    })
    assert response.status_code == 403


def test_book_and_cancel_appointment_via_web(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    # future_date is a weekday - the test business's working_hours (see
    # register_business_web) is open 09:00-18:00 on every weekday.
    create = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": future_date, "time": "10:00",
        "customer_name": "Test Customer", "service": "Haircut",
    })
    assert create.status_code == 302

    day_view = client.get(f"/schedule?date={future_date}")
    body = day_view.get_data(as_text=True)
    assert "Test Customer" in body
    assert "Haircut" in body

    # Extract the appointment id from the rendered cancel form's action URL.
    match = re.search(r"/schedule/appointments/(\d+)/cancel", body)
    assert match is not None
    appointment_id = match.group(1)

    cancel_csrf = get_csrf_token(client, f"/schedule?date={future_date}")
    cancel = web_post(client, f"/schedule/appointments/{appointment_id}/cancel", {
        "csrf_token": cancel_csrf, "date": future_date, "view": "day",
    })
    assert cancel.status_code == 302

    after_cancel = client.get(f"/schedule?date={future_date}").get_data(as_text=True)
    assert "Test Customer" not in after_cancel


def test_double_booking_the_same_slot_is_rejected(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)

    csrf_1 = get_csrf_token(client, "/schedule")
    first = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_1, "date": future_date, "time": "11:00", "customer_name": "First Customer",
    })
    assert first.status_code == 302

    csrf_2 = get_csrf_token(client, "/schedule")
    second = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_2, "date": future_date, "time": "11:00", "customer_name": "Second Customer",
    })
    assert second.status_code == 302
    assert "book_error=1" in second.headers["Location"]

    body = client.get(f"/schedule?date={future_date}").get_data(as_text=True)
    assert "Second Customer" not in body
    assert "First Customer" in body


def test_book_appointment_with_custom_duration_blocks_the_slots_it_covers(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    # 90-minute booking at 09:00 on a 30-minute grid (the test business's default, see
    # register_business_web) should end at 10:30 and swallow the 09:30 row too.
    create = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": future_date, "time": "09:00",
        "customer_name": "Ana", "duration": "90",
    })
    assert create.status_code == 302

    body = client.get(f"/schedule?date={future_date}").get_data(as_text=True)
    assert "09:00" in body and "10:30" in body  # the booked card's own start-end range
    assert "schedule-slot-covered" in body  # 09:30's row is blocked, not a free "Book" button

    # The 09:30 slot can't be double-booked even via a direct POST, since it overlaps
    # Ana's 09:00-10:30 appointment.
    csrf_2 = get_csrf_token(client, f"/schedule?date={future_date}")
    second = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_2, "date": future_date, "time": "09:30", "customer_name": "Overlap Attempt",
    })
    assert "book_error=1" in second.headers["Location"]


def test_book_appointment_with_custom_duration_field(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    # duration="custom" defers to duration_custom, same as the "Custom" option on the
    # per-slot picker (see schedule.html/pickers.js).
    create = web_post(client, "/schedule/appointments", {
        "csrf_token": csrf_token, "date": future_date, "time": "09:00",
        "customer_name": "Ivan", "duration": "custom", "duration_custom": "20",
    })
    assert create.status_code == 302

    body = client.get(f"/schedule?date={future_date}").get_data(as_text=True)
    assert "09:00" in body and "09:20" in body


def test_update_slot_duration_accepts_custom_value(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    response = web_post(client, "/schedule/slot-duration", {
        "csrf_token": csrf_token, "slot_duration": "50", "date": future_date,
    })
    assert response.status_code == 302

    business_id = database.business_by_username(unique_username)[0]
    assert database.get_slot_duration(business_id) == 50


def test_update_slot_duration_rejects_out_of_range_value(client, unique_username, unique_email, future_date):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/schedule")

    web_post(client, "/schedule/slot-duration", {
        "csrf_token": csrf_token, "slot_duration": "9999", "date": future_date,
    })

    business_id = database.business_by_username(unique_username)[0]
    assert database.get_slot_duration(business_id) == 30  # falls back to the default


def test_home_redirects_to_subscription_once_access_lapses(client, unique_username, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    register_business_web(client, unique_username, unique_email)
    business_id = database.business_by_username(unique_username)[0]
    # This test is about the subscription gate, not the (Faza 13) onboarding one that
    # now also sits in front of home() - complete it here so home()'s redirect below is
    # unambiguously the subscription gate firing, not the wizard.
    database.mark_onboarding_completed(business_id)

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
