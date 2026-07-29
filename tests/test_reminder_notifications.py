"""Owner appointment reminders (reminders.py) used to be an email - they now create an
in-app notification (database.notifications, surfaced by the web bell icon and the
mobile app) plus a best-effort OS push instead. This covers that behavior directly
against the real CI Postgres, same pattern as test_appointments_db.py."""
from datetime import timedelta

import app as app_module
import database
import reminders as reminders_module
from booking import BUSINESS_TZ, now_local
from conftest import register_business


def test_owner_reminder_creates_notification_and_attempts_push_instead_of_email(client, unique_username, unique_email, monkeypatch):
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]

    database.set_owner_reminders_enabled(business_id, True)
    database.set_reminder_minutes(business_id, 1440, 60)  # customer 24h, owner 1h

    start = (now_local() + timedelta(minutes=60)).astimezone(BUSINESS_TZ)
    end = start + timedelta(minutes=30)
    database.create_appointment(business_id, "Ana", start, end, service="Haircut")

    sent_emails = []
    pushes = []
    monkeypatch.setattr(reminders_module, "send_email", lambda **kwargs: sent_emails.append(kwargs))
    monkeypatch.setattr(reminders_module, "send_push_to_business", lambda *args, **kwargs: pushes.append((args, kwargs)))

    reminders_module.send_appointment_reminders_if_needed()

    assert sent_emails == []  # no more owner email
    assert len(pushes) == 1
    assert pushes[0][0][0] == business_id

    rows = database.notifications_for_business(business_id)
    assert len(rows) == 1
    assert rows[0][1] == "Upcoming appointment reminder"
    assert "Ana" in rows[0][2]
    assert database.unread_notification_count(business_id) == 1

    # A second run within the same tolerance window must not double-send.
    reminders_module.send_appointment_reminders_if_needed()
    assert len(database.notifications_for_business(business_id)) == 1


def test_owner_reminder_fires_under_beta_free_mode_without_real_pro_access(client, unique_username, unique_email, monkeypatch):
    # BETA_FREE_MODE unlocks the reminders toggle in Settings for every business
    # regardless of real subscription status (same as Schedule/upsell) - the send-time
    # re-check here must honor that too, not just has_pro_access() alone, or a
    # BETA_FREE_MODE business could turn the toggle on and never actually get a reminder.
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")
    assert database.has_pro_access(business_id) is False

    database.set_owner_reminders_enabled(business_id, True)
    database.set_reminder_minutes(business_id, 1440, 60)
    start = (now_local() + timedelta(minutes=60)).astimezone(BUSINESS_TZ)
    database.create_appointment(business_id, "Bob", start, start + timedelta(minutes=30))

    monkeypatch.setattr(reminders_module, "send_push_to_business", lambda *args, **kwargs: None)
    reminders_module.send_appointment_reminders_if_needed()

    assert len(database.notifications_for_business(business_id)) == 1


def test_owner_reminder_skipped_without_pro_access_when_beta_free_mode_is_off(client, unique_username, unique_email, monkeypatch):
    # Once BETA_FREE_MODE is turned off (real launch), a non-Pro business must go back to
    # getting nothing - same real gating as every other Pro-only feature.
    monkeypatch.setattr(app_module, "BETA_FREE_MODE", False)
    reg = register_business(client, unique_username, unique_email)
    business_id = reg.get_json()["business_id"]
    database.link_subscription(business_id, "cust", "sub", "active", plan="starter")

    database.set_owner_reminders_enabled(business_id, True)
    database.set_reminder_minutes(business_id, 1440, 60)
    start = (now_local() + timedelta(minutes=60)).astimezone(BUSINESS_TZ)
    database.create_appointment(business_id, "Cara", start, start + timedelta(minutes=30))

    reminders_module.send_appointment_reminders_if_needed()

    assert database.notifications_for_business(business_id) == []


def test_relative_when_today_tomorrow_and_further_out():
    # Reads naturally in the reminder text - no date at all for a same-day appointment
    # (just the time), "tomorrow at HH:MM" for the day after, and a full date only once
    # neither of those words would be accurate.
    today = now_local()
    assert reminders_module._relative_when(today) == f"at {today.strftime('%H:%M')}"
    tomorrow = today + timedelta(days=1)
    assert reminders_module._relative_when(tomorrow) == f"tomorrow at {tomorrow.strftime('%H:%M')}"
    later = today + timedelta(days=5)
    assert reminders_module._relative_when(later) == f"{later.strftime('%A, %B %d')} at {later.strftime('%H:%M')}"
