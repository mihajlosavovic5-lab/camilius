"""Owner appointment reminders (reminders.py) used to be an email - they now create an
in-app notification (database.notifications, surfaced by the web bell icon and the
mobile app) plus a best-effort OS push instead. This covers that behavior directly
against the real CI Postgres, same pattern as test_appointments_db.py."""
from datetime import timedelta

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
