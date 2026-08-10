"""Owner appointment reminders (reminders.py) used to be an email - they now create an
in-app notification (database.notifications, surfaced by the web bell icon and the
mobile app) plus a best-effort OS push instead. This covers that behavior directly
against the real CI Postgres, same pattern as test_appointments_db.py."""
from datetime import timedelta

import app as app_module
import database
import reminders as reminders_module
import booking
from booking import DEFAULT_TZ, now_local
from conftest import register_business


def test_owner_reminder_creates_notification_and_attempts_push_instead_of_email(client, unique_email, monkeypatch):
    reg = register_business(client, unique_email)
    business_id = reg.get_json()["business_id"]

    database.set_owner_reminders_enabled(business_id, True)
    database.set_owner_reminder_minutes(business_id, 60)

    start = (now_local() + timedelta(minutes=60)).astimezone(DEFAULT_TZ)
    end = start + timedelta(minutes=30)
    database.create_appointment(business_id, "Ana", start, end, service="Haircut")

    sent_emails = []
    pushes = []
    monkeypatch.setattr(reminders_module, "send_email", lambda **kwargs: sent_emails.append(kwargs))
    monkeypatch.setattr(reminders_module, "send_push_to_business", lambda *args, **kwargs: pushes.append((args, kwargs)))

    reminders_module.send_appointment_reminders_if_needed()

    assert sent_emails == []  # no more owner email
    # Filtered to this test's own business rather than asserting on `pushes` as a whole -
    # this shares one real, un-truncated Postgres DB across the whole suite (see
    # conftest.py), so a still-"due" appointment left behind by an earlier run (business
    # id differs every time via the unique_email fixture, but
    # send_appointment_reminders_if_needed() scans every business, not just this one)
    # can otherwise inflate this count without this test having done anything wrong.
    own_pushes = [call for call in pushes if call[0][0] == business_id]
    assert len(own_pushes) == 1

    rows = database.notifications_for_business(business_id)
    assert len(rows) == 1
    assert rows[0][1] == "Upcoming appointment reminder"
    assert "Ana" in rows[0][2]
    assert database.unread_notification_count(business_id) == 1

    # A second run within the same tolerance window must not double-send.
    reminders_module.send_appointment_reminders_if_needed()
    assert len(database.notifications_for_business(business_id)) == 1


def test_owner_reminder_fires_under_beta_free_mode_without_real_pro_access(client, unique_email, monkeypatch):
    # BETA_FREE_MODE unlocks the reminders toggle in Settings for every business
    # regardless of real subscription status (same as Schedule/upsell) - the send-time
    # re-check here must honor that too, not just has_pro_access() alone, or a
    # BETA_FREE_MODE business could turn the toggle on and never actually get a reminder.
    # Explicitly pinned True (F4 flipped the module's own default to False for the real
    # launch) - this still needs covering, since the flag stays in the code for if
    # billing ever needs to go dormant again, same as its sibling test below pins False.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", True)
    reg = register_business(client, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")
    assert database.has_pro_access(business_id) is False

    database.set_owner_reminders_enabled(business_id, True)
    database.set_owner_reminder_minutes(business_id, 60)
    start = (now_local() + timedelta(minutes=60)).astimezone(DEFAULT_TZ)
    database.create_appointment(business_id, "Bob", start, start + timedelta(minutes=30))

    monkeypatch.setattr(reminders_module, "send_push_to_business", lambda *args, **kwargs: None)
    reminders_module.send_appointment_reminders_if_needed()

    assert len(database.notifications_for_business(business_id)) == 1


def test_owner_reminder_skipped_without_pro_access_when_beta_free_mode_is_off(client, unique_email, monkeypatch):
    # Once BETA_FREE_MODE is turned off (real launch), a non-Pro business must go back to
    # getting nothing - same real gating as every other Pro-only feature.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    reg = register_business(client, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    database.set_owner_reminders_enabled(business_id, True)
    database.set_owner_reminder_minutes(business_id, 60)
    start = (now_local() + timedelta(minutes=60)).astimezone(DEFAULT_TZ)
    database.create_appointment(business_id, "Cara", start, start + timedelta(minutes=30))

    reminders_module.send_appointment_reminders_if_needed()

    assert database.notifications_for_business(business_id) == []


_ALL_DAYS_OPEN = "\n".join(f"{day}: 00:00-23:59" for day in
                           ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))


def _safe_future_slot():
    """A booking slot guaranteed to fall safely inside _ALL_DAYS_OPEN's hours regardless of
    what time of day the test happens to run - tomorrow at 10:00 local time, unlike
    `now_local() + timedelta(hours=2)` (used below), which spills past the current day's
    23:59 close time - and so gets rejected as unavailable - whenever the suite happens to
    run in the last ~2.5 hours before local midnight."""
    tomorrow = (now_local() + timedelta(days=1)).astimezone(DEFAULT_TZ)
    return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)


def test_ai_booking_notifies_owner_of_new_appointment(client, unique_email, monkeypatch):
    # A customer booking themselves in via the AI chat (booking.attempt_ai_booking,
    # the AI's tool executor) should tell the owner right away - independent of the
    # "Remind me" reminder toggle/lead time, which is a separate, later notification.
    reg = register_business(client, unique_email, working_hours=_ALL_DAYS_OPEN)
    business_id = reg.get_json()["business_id"]
    business = database.business_by_id(business_id)

    pushes = []
    monkeypatch.setattr(booking, "send_push_to_business", lambda *args, **kwargs: pushes.append((args, kwargs)))

    requested = _safe_future_slot()
    result = booking.attempt_ai_booking(
        business, business_id, 30, requested.isoformat(), "Pera", "igsid_1", service="Haircut",
    )

    assert result["status"] == "booked"
    rows = database.notifications_for_business(business_id)
    assert len(rows) == 1
    assert rows[0][1] == "New appointment booked"
    assert "Pera" in rows[0][2]
    assert "Haircut" in rows[0][2]
    assert len(pushes) == 1
    assert pushes[0][0][0] == business_id


def test_ai_booking_notification_respects_its_own_toggle(client, unique_email, monkeypatch):
    reg = register_business(client, unique_email, working_hours=_ALL_DAYS_OPEN)
    business_id = reg.get_json()["business_id"]
    business = database.business_by_id(business_id)
    database.set_new_booking_notifications_enabled(business_id, False)

    monkeypatch.setattr(booking, "send_push_to_business", lambda *args, **kwargs: None)
    requested = _safe_future_slot()
    result = booking.attempt_ai_booking(business, business_id, 30, requested.isoformat(), "Ana", "igsid_2")

    assert result["status"] == "booked"  # the booking itself always succeeds
    assert database.notifications_for_business(business_id) == []


def test_relative_when_today_tomorrow_and_further_out():
    # Reads naturally in the reminder text - no date at all for a same-day appointment
    # (just the time), "tomorrow at HH:MM" for the day after, and a full date only once
    # neither of those words would be accurate.
    today = now_local()
    assert booking.relative_when(today) == f"at {today.strftime('%H:%M')}"
    tomorrow = today + timedelta(days=1)
    assert booking.relative_when(tomorrow) == f"tomorrow at {tomorrow.strftime('%H:%M')}"
    later = today + timedelta(days=5)
    assert booking.relative_when(later) == f"{later.strftime('%A, %B %d')} at {later.strftime('%H:%M')}"
