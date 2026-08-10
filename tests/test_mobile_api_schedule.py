"""Tests for the mobile app's native Schedule tab - the /api/schedule* JSON routes that
mirror the website's /schedule day/week calendar (see api.py's api_schedule and friends),
so the calendar can be shown as its own bottom-tab screen instead of bridging out to the
website in an in-app browser."""
import app as app_module
import database
from conftest import register_and_login

WORKING_HOURS = ("Monday: 09:00-18:00\nTuesday: 09:00-18:00\nWednesday: 09:00-18:00\n"
                  "Thursday: 09:00-18:00\nFriday: 09:00-18:00\nSaturday: closed\nSunday: closed")


def _auth_header(client, email, **overrides):
    overrides.setdefault("working_hours", WORKING_HOURS)
    token, business_id = register_and_login(client, email, **overrides)
    return {"Authorization": f"Bearer {token}"}, business_id


def test_schedule_requires_auth(client):
    response = client.get("/api/schedule")
    assert response.status_code == 401


def test_schedule_locked_for_starter_plan(client, unique_email, monkeypatch):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.get("/api/schedule", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["locked"] is True


def test_schedule_day_view_unlocked_during_trial(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    # future_date is a weekday - within the test business's working hours (09:00-18:00).
    response = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["locked"] is False
    assert data["view"] == "day"
    assert data["has_any_open_day"] is True
    times = [row["time"] for row in data["slot_rows"]]
    assert "10:00" in times
    assert all(row["appointment"] is None for row in data["slot_rows"])


def test_schedule_week_view(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    response = client.get(f"/api/schedule?view=week&date={future_date}", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["view"] == "week"
    assert len(data["days"]) == 7
    # Saturday/Sunday are closed per WORKING_HOURS above.
    closed_days = [d for d in data["days"] if d["closed"]]
    assert len(closed_days) == 2


def test_book_and_cancel_appointment(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    create = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "10:00", "customer_name": "Test Customer", "service": "Haircut",
    })
    assert create.status_code == 200
    assert create.get_json()["success"] is True

    day_view = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers).get_json()
    booked_row = next(row for row in day_view["slot_rows"] if row["time"] == "10:00")
    assert booked_row["appointment"]["customer_name"] == "Test Customer"
    assert booked_row["appointment"]["service"] == "Haircut"
    appointment_id = booked_row["appointment"]["id"]

    cancel = client.post(f"/api/schedule/appointments/{appointment_id}/cancel", headers=headers)
    assert cancel.status_code == 200
    assert cancel.get_json()["success"] is True

    after_cancel = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers).get_json()
    freed_row = next(row for row in after_cancel["slot_rows"] if row["time"] == "10:00")
    assert freed_row["appointment"] is None


def test_double_booking_the_same_slot_is_rejected(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    first = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "11:00", "customer_name": "First Customer",
    })
    assert first.status_code == 200

    second = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "11:00", "customer_name": "Second Customer",
    })
    assert second.status_code == 409
    assert second.get_json()["error"] == "book_error"


def test_book_appointment_forbidden_on_starter_plan(client, unique_email, monkeypatch, future_date):
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    headers, business_id = _auth_header(client, unique_email)
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    response = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "10:00", "customer_name": "Bypass Attempt",
    })
    assert response.status_code == 403


def test_update_slot_duration(client, unique_email, future_date):
    headers, business_id = _auth_header(client, unique_email)

    response = client.post("/api/schedule/slot-duration", headers=headers, json={"slot_duration": 60})
    assert response.status_code == 200
    assert response.get_json()["slot_duration"] == 60
    assert database.get_slot_duration(business_id) == 60

    schedule = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers).get_json()
    assert schedule["slot_minutes"] == 60


def test_update_slot_duration_accepts_custom_value(client, unique_email):
    headers, business_id = _auth_header(client, unique_email)

    response = client.post("/api/schedule/slot-duration", headers=headers, json={"slot_duration": 50})
    assert response.status_code == 200
    assert response.get_json()["slot_duration"] == 50
    assert database.get_slot_duration(business_id) == 50


def test_update_slot_duration_rejects_out_of_range_value(client, unique_email):
    headers, business_id = _auth_header(client, unique_email)

    client.post("/api/schedule/slot-duration", headers=headers, json={"slot_duration": 9999})
    assert database.get_slot_duration(business_id) == 30  # falls back to the default


def test_book_appointment_with_custom_duration_blocks_the_slots_it_covers(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    # 90-minute booking at 09:00 on the 30-minute default grid should end at 10:30 and
    # swallow the 09:30 row too.
    create = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "09:00", "customer_name": "Ana", "duration": 90,
    })
    assert create.status_code == 200

    day_view = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers).get_json()
    booked_row = next(row for row in day_view["slot_rows"] if row["time"] == "09:00")
    assert booked_row["appointment"]["start_time"] == "09:00"
    assert booked_row["appointment"]["end_time"] == "10:30"

    covered_row = next(row for row in day_view["slot_rows"] if row["time"] == "09:30")
    assert covered_row["appointment"] is None
    assert covered_row["covered_by"]["customer_name"] == "Ana"

    # The covered slot can't be double-booked even via a direct POST, since it overlaps
    # Ana's 09:00-10:30 appointment.
    overlap = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "09:30", "customer_name": "Overlap Attempt",
    })
    assert overlap.status_code == 409


def test_book_appointment_with_out_of_range_duration_falls_back_to_business_default(client, unique_email, future_date):
    headers, _ = _auth_header(client, unique_email)

    create = client.post("/api/schedule/appointments", headers=headers, json={
        "date": future_date, "time": "09:00", "customer_name": "Ivan", "duration": 9999,
    })
    assert create.status_code == 200

    day_view = client.get(f"/api/schedule?view=day&date={future_date}", headers=headers).get_json()
    booked_row = next(row for row in day_view["slot_rows"] if row["time"] == "09:00")
    assert booked_row["appointment"]["end_time"] == "09:30"  # 30-minute business default
