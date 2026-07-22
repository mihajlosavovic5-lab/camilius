"""No-show reminders (Faza 9, Pro plan) - sends an Instagram DM reminder ~24h and ~2h
before an upcoming appointment. Runs from process_emails.py's background loop, not
inline with request handling, since it has nothing to do with any single incoming
webhook call.

Only appointments booked BY THE AI over Instagram DM (source has a real sender_igsid -
see database.appointments_needing_reminders) can be reminded at all; a manually-entered
appointment has no Instagram thread to send to.

Known platform limitation, accepted rather than worked around (same as the existing
manual "Send reply" flow's 24h-window handling): Instagram only allows a business to
message a customer within 24 hours of that customer's OWN last message, unless using a
Meta-approved message tag this app doesn't have. In practice this means the 24h-before
reminder usually goes through fine (often still inside that window from the booking
conversation itself), but the 2h-before reminder for an appointment booked more than a
day in advance will frequently fail - Instagram rejects the send, we log it and move on,
exactly like a manual reply sent outside the window already does elsewhere in this app.
"""
from datetime import timedelta

import requests

from database import (
    appointments_needing_reminders, mark_reminder_sent, business_by_id,
    is_reminders_enabled, has_pro_access, log_client_error,
)
from booking import BUSINESS_TZ, now_local
from instagram_api import send_message as send_instagram_message

WINDOW_24H = (timedelta(hours=23, minutes=45), timedelta(hours=24, minutes=15))
WINDOW_2H = (timedelta(hours=1, minutes=45), timedelta(hours=2, minutes=15))


def _reminder_text(business_name, start_local, which):
    when = start_local.strftime("%A at %H:%M")
    if which == "24h":
        return f"Reminder: you have an appointment with {business_name} tomorrow, {when}. See you then!"
    return f"Reminder: your appointment with {business_name} is coming up today at {start_local.strftime('%H:%M')}. See you soon!"


def send_appointment_reminders_if_needed():
    now = now_local()
    business_cache = {}

    for row in appointments_needing_reminders():
        appointment_id, business_id, customer_name, sender_igsid, service, start_time, sent_24h, sent_2h = row

        if business_id not in business_cache:
            business_cache[business_id] = business_by_id(business_id)
        business = business_cache[business_id]
        if business is None or not business[5] or not business[7]:
            continue  # no Instagram connected (shouldn't happen for a booked appointment, but be defensive)

        # Re-checked here (not just at toggle-time) so a business that downgraded off
        # Pro after turning this on stops getting reminders sent immediately, without
        # needing to separately flip the toggle back off.
        if not (is_reminders_enabled(business_id) and has_pro_access(business_id)):
            continue

        # Aware-datetime subtraction is instant-based regardless of each side's specific
        # tzinfo representation, so start_time (whatever offset psycopg2 returned it in)
        # minus now (BUSINESS_TZ) needs no extra conversion to be correct.
        time_until = start_time - now
        which = None
        if sent_24h is None and WINDOW_24H[0] <= time_until <= WINDOW_24H[1]:
            which = "24h"
        elif sent_2h is None and WINDOW_2H[0] <= time_until <= WINDOW_2H[1]:
            which = "2h"
        if which is None:
            continue

        business_name, access_token, ig_account_id = business[1], business[7], business[5]
        text = _reminder_text(business_name, start_time.astimezone(BUSINESS_TZ), which)
        try:
            send_instagram_message(sender_igsid, text, access_token, ig_account_id)
            print(f"[reminders] Sent {which} reminder for appointment_id={appointment_id} business_id={business_id}", flush=True)
        except requests.RequestException as error:
            # Most commonly Instagram's 24h messaging-window policy (see module
            # docstring) - not a bug, but still marked sent below so we don't retry this
            # exact window forever for the same appointment.
            error_body = error.response.text if getattr(error, "response", None) is not None else str(error)
            print(f"[reminders] Failed to send {which} reminder for appointment_id={appointment_id}: {error_body}", flush=True)
            log_client_error(business_id, f"reminder send failed ({which}): {error_body}", "", f"reminder-debug appointment_id={appointment_id}", "")

        mark_reminder_sent(appointment_id, which)
