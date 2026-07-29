"""Direct database.py tests for appointments and Pro-access gating (Faza 8-10) - calls
the DB functions directly against the real CI Postgres, same pattern as
test_delete_business.py, rather than going through HTTP routes."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import database
from conftest import register_business

TZ = ZoneInfo("Europe/Belgrade")


def _future_slot(hours_from_now=48):
    start = datetime.now(TZ).replace(minute=0, second=0, microsecond=0) + timedelta(hours=hours_from_now)
    return start, start + timedelta(minutes=30)


def test_create_appointment_shows_up_in_range_and_cancel_removes_it(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    start, end = _future_slot()

    appointment_id = database.create_appointment(business_id, "Ana", start, end, service="Haircut")
    found = database.appointments_in_range(business_id, start, end)
    assert len(found) == 1
    assert found[0][1] == "Ana"  # customer_name
    assert found[0][3] == "Haircut"  # service

    database.cancel_appointment(appointment_id)
    assert database.appointments_in_range(business_id, start, end) == []


def test_appointments_in_range_only_returns_overlapping_confirmed_ones(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    start, end = _future_slot(hours_from_now=72)

    database.create_appointment(business_id, "Bob", start, end)
    # An appointment well outside the queried window must not show up.
    later_start, later_end = _future_slot(hours_from_now=200)
    database.create_appointment(business_id, "Cara", later_start, later_end)

    found = database.appointments_in_range(business_id, start, end)
    assert len(found) == 1
    assert found[0][1] == "Bob"


def test_next_upcoming_appointment_for_sender(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    start, end = _future_slot(hours_from_now=24)

    assert database.next_upcoming_appointment_for_sender(business_id, "igsid_dan") is None

    appointment_id = database.create_appointment(business_id, "Dan", start, end, sender_igsid="igsid_dan", service="Manicure")
    found = database.next_upcoming_appointment_for_sender(business_id, "igsid_dan")
    assert found is not None
    assert found[1] == "Dan"
    assert found[2] == "Manicure"

    # A canceled appointment must not be returned as "upcoming".
    database.cancel_appointment(appointment_id)
    assert database.next_upcoming_appointment_for_sender(business_id, "igsid_dan") is None


def test_slot_duration_default_and_round_trip(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.get_slot_duration(business_id) == 30  # column default
    database.set_slot_duration(business_id, 60)
    assert database.get_slot_duration(business_id) == 60


def test_upsell_pairs_round_trip(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.get_upsell_pairs(business_id) is None
    database.set_upsell_pairs(business_id, "Haircut -> Treatment")
    assert database.get_upsell_pairs(business_id) == "Haircut -> Treatment"


def test_reminders_enabled_defaults_off_and_round_trips(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.is_reminders_enabled(business_id) is False
    database.set_reminders_enabled(business_id, True)
    assert database.is_reminders_enabled(business_id) is True


def test_owner_reminders_enabled_defaults_off_and_round_trips_independently(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.is_owner_reminders_enabled(business_id) is False
    database.set_owner_reminders_enabled(business_id, True)
    assert database.is_owner_reminders_enabled(business_id) is True
    # Independent from the customer-facing toggle - flipping one must not affect the other.
    assert database.is_reminders_enabled(business_id) is False


def test_new_booking_notifications_enabled_defaults_on_and_round_trips(client, unique_username, unique_email):
    # Default ON since this is a continuation of the previously-unconditional new-booking
    # notification behavior, not something existing users need to opt into.
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.is_new_booking_notifications_enabled(business_id) is True
    database.set_new_booking_notifications_enabled(business_id, False)
    assert database.is_new_booking_notifications_enabled(business_id) is False


def test_reminder_minutes_default_and_round_trip(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.get_reminder_minutes(business_id) == (1440, 60)  # column defaults: 24h, 1h
    database.set_reminder_minutes(business_id, 120, 45)
    assert database.get_reminder_minutes(business_id) == (120, 45)


def test_has_pro_access_during_self_serve_trial(client, unique_username, unique_email):
    # A brand new account is 'trialing' with no plan chosen yet - gets full Pro-level
    # access for the length of the trial (see database.has_pro_access's docstring).
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    assert database.get_subscription_status(business_id) == "trialing"
    assert database.has_pro_access(business_id) is True


def test_has_pro_access_starter_plan_is_false(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust_1", "sub_1", "active", plan="starter")
    assert database.has_pro_access(business_id) is False
    assert database.effective_subscription_active(business_id) is True  # still a paying, active customer


def test_has_pro_access_pro_plan_is_true(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust_2", "sub_2", "active", plan="pro")
    assert database.has_pro_access(business_id) is True


def test_has_pro_access_canceled_subscription_is_false(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust_3", "sub_3", "active", plan="pro")
    database.update_subscription_status("sub_3", "canceled")
    assert database.has_pro_access(business_id) is False
    assert database.effective_subscription_active(business_id) is False


def test_stripe_subscription_link_writes_shared_status_and_plan_columns(client, unique_username, unique_email):
    # link_stripe_subscription/update_stripe_subscription_status are a separate code
    # path from the Paddle ones above, but must land on the exact same processor-
    # agnostic subscription_status/subscription_plan columns has_pro_access() reads.
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_stripe_subscription(business_id, "stripe_cust_1", "stripe_sub_1", "active", plan="pro")
    assert database.get_subscription_status(business_id) == "active"
    assert database.get_subscription_plan(business_id) == "pro"
    assert database.has_pro_access(business_id) is True
    assert database.get_stripe_customer_id(business_id) == "stripe_cust_1"

    database.update_stripe_subscription_status("stripe_sub_1", "canceled")
    assert database.get_subscription_status(business_id) == "canceled"
    assert database.has_pro_access(business_id) is False


def test_notifications_round_trip_and_mark_read(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    assert database.notifications_for_business(business_id) == []
    assert database.unread_notification_count(business_id) == 0

    database.create_notification(business_id, "Upcoming appointment reminder", "You have an appointment with Ana in 1 hour.")
    database.create_notification(business_id, "Upcoming appointment reminder", "You have an appointment with Bob in 1 hour.")

    rows = database.notifications_for_business(business_id)
    assert len(rows) == 2
    assert rows[0][1] == "Upcoming appointment reminder"
    assert all(row[5] is None for row in rows)  # read_at
    assert database.unread_notification_count(business_id) == 2

    # Marking a single notification read only clears that one.
    database.mark_notifications_read(business_id, rows[0][0])
    assert database.unread_notification_count(business_id) == 1

    # Marking with no id clears every remaining unread notification.
    database.mark_notifications_read(business_id)
    assert database.unread_notification_count(business_id) == 0


def test_delete_business_cascades_appointments(client, unique_username, unique_email):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    start, end = _future_slot()
    database.create_appointment(business_id, "Eve", start, end)

    database.delete_business(business_id)

    assert database.business_by_id(business_id) is None
    # The appointment row must be gone too, not orphaned with a dangling FK.
    assert database.appointments_in_range(business_id, start, end) == []
