"""Scheduled email/DM/notification jobs run from process_emails.py's background loop
rather than inline with request handling, since neither has anything to do with any
single incoming webhook call:

- Appointment reminders (Faza 9/12, Pro plan) - two independent, independently-timed
  reminders per appointment: a customer-facing Instagram DM, and an owner-facing in-app
  notification + push (send_appointment_reminders_if_needed). Each has its own on/off
  toggle and its own single configurable lead time (business.customer_reminder_minutes /
  owner_reminder_minutes) rather than the old fixed 24h-and-2h pair.
- Trial-ending warning emails - see send_trial_ending_warnings_if_needed below.

The customer DM can only go out for appointments booked BY THE AI over Instagram DM
(source has a real sender_igsid - see database.appointments_needing_reminders); a
manually-entered appointment has no Instagram thread to send to. The owner notification
has no such restriction - it's checked independently of sender_igsid.

Known platform limitation, accepted rather than worked around (same as the existing
manual "Send reply" flow's 24h-window handling): Instagram only allows a business to
message a customer within 24 hours of that customer's OWN last message, unless using a
Meta-approved message tag this app doesn't have. A customer reminder lead time longer
than that (the 24h default included) will frequently fail to send for an appointment
booked further in advance than its own lead time - Instagram rejects the send, we log it
and move on, exactly like a manual reply sent outside the window already does elsewhere
in this app.
"""
import os
from datetime import timedelta

import requests

from database import (
    appointments_needing_reminders, mark_reminder_sent, business_by_id,
    is_reminders_enabled, is_owner_reminders_enabled, get_reminder_minutes,
    has_pro_access, log_client_error, create_notification, is_push_enabled,
    businesses_needing_trial_warning, mark_trial_warning_sent,
)
from booking import BUSINESS_TZ, now_local, relative_when
from instagram_api import send_message as send_instagram_message
from send_email import send_email
from push_notifications import send_push_to_business

# How close start_time - now needs to be to a business's configured lead time before
# that reminder fires - the loop that calls send_appointment_reminders_if_needed runs
# far more often than this, so a +/-15 minute band is plenty to catch it exactly once.
REMINDER_TOLERANCE = timedelta(minutes=15)


def _format_lead_time(minutes):
    if minutes >= 1440 and minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} day" + ("s" if days != 1 else "")
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{minutes} minute" + ("s" if minutes != 1 else "")


def _customer_reminder_text(business_name, start_local, minutes):
    when = relative_when(start_local)
    return f"Reminder: you have an appointment with {business_name} in {_format_lead_time(minutes)} ({when}). See you then!"


def _owner_reminder_notification_body(customer_name, service, start_local, minutes):
    when = relative_when(start_local)
    service_part = f" for {service}" if service else ""
    return (
        f"You have an appointment with {customer_name}{service_part} in "
        f"{_format_lead_time(minutes)}, {when}."
    )


def send_appointment_reminders_if_needed():
    from app import BETA_FREE_MODE  # deferred: avoid circular import at module load

    now = now_local()
    business_cache = {}

    for row in appointments_needing_reminders():
        (appointment_id, business_id, customer_name, sender_igsid, service, start_time,
         reminder_customer_sent_at, reminder_owner_sent_at) = row

        if business_id not in business_cache:
            business_cache[business_id] = business_by_id(business_id)
        business = business_cache[business_id]
        if business is None:
            continue

        # Re-checked here (not just at toggle-time) so a business that downgraded off
        # Pro after turning this on stops getting reminders sent immediately, without
        # needing to separately flip the toggle back off. BETA_FREE_MODE included so this
        # matches every other Pro gate (Schedule, upsell, the settings toggle itself) -
        # without it, a BETA_FREE_MODE business could turn the toggle on but never
        # actually get a reminder, since has_pro_access() alone ignores that override.
        if not (BETA_FREE_MODE or has_pro_access(business_id)):
            continue

        # Aware-datetime subtraction is instant-based regardless of each side's specific
        # tzinfo representation, so start_time (whatever offset psycopg2 returned it in)
        # minus now (BUSINESS_TZ) needs no extra conversion to be correct.
        time_until = start_time - now
        start_local = start_time.astimezone(BUSINESS_TZ)
        business_name, ig_account_id, access_token = business[1], business[5], business[7]
        customer_minutes, owner_minutes = get_reminder_minutes(business_id)

        if (reminder_customer_sent_at is None and is_reminders_enabled(business_id)
                and sender_igsid and ig_account_id and access_token
                and abs(time_until - timedelta(minutes=customer_minutes)) <= REMINDER_TOLERANCE):
            text = _customer_reminder_text(business_name, start_local, customer_minutes)
            try:
                send_instagram_message(sender_igsid, text, access_token, ig_account_id)
                print(f"[reminders] Sent customer reminder for appointment_id={appointment_id} business_id={business_id}", flush=True)
            except requests.RequestException as error:
                # Most commonly Instagram's 24h messaging-window policy (see module
                # docstring) - not a bug, but still marked sent below so we don't retry
                # this exact window forever for the same appointment.
                error_body = error.response.text if getattr(error, "response", None) is not None else str(error)
                print(f"[reminders] Failed to send customer reminder for appointment_id={appointment_id}: {error_body}", flush=True)
                log_client_error(business_id, f"reminder send failed (customer): {error_body}", "", f"reminder-debug appointment_id={appointment_id}", "")
            mark_reminder_sent(appointment_id, "customer")

        if (reminder_owner_sent_at is None and is_owner_reminders_enabled(business_id)
                and is_push_enabled(business_id)
                and abs(time_until - timedelta(minutes=owner_minutes)) <= REMINDER_TOLERANCE):
            title = "Upcoming appointment reminder"
            body = _owner_reminder_notification_body(customer_name, service, start_local, owner_minutes)
            try:
                # In-app (bell icon on web, notifications screen on mobile) so this always
                # shows up somewhere regardless of push setup, plus a real OS push
                # (no-op until Firebase is configured/the device has no token - see
                # push_notifications.py) for an actual phone notification.
                create_notification(business_id, title, body)
                send_push_to_business(business_id, title, body, data={
                    "type": "appointment_reminder", "appointment_id": str(appointment_id),
                })
                print(f"[reminders] Sent owner reminder for appointment_id={appointment_id} business_id={business_id}", flush=True)
            except Exception as error:
                print(f"[reminders] Failed to send owner reminder for appointment_id={appointment_id}: {error}", flush=True)
                log_client_error(business_id, f"reminder send failed (owner): {error}", "", f"reminder-debug appointment_id={appointment_id}", "")
            mark_reminder_sent(appointment_id, "owner")


def _trial_ending_email_body(business_name, trial_ends_at):
    when = trial_ends_at.strftime("%B %d")
    return (
        f"Hi {business_name},\n\n"
        f"Your 14-day free trial ends on {when}. After that, we'll charge the card on "
        f"file to keep the AI processing your Instagram messages without interruption.\n\n"
        f"Nothing to do if you want to continue - it's automatic. If you'd rather not "
        f"continue, cancel any time before then from Subscription in your dashboard, "
        f"and you won't be charged anything.\n\n"
        f"- Camilius"
    )


def send_trial_ending_warnings_if_needed():
    """Paddle Billing has no customer.subscription.trial_will_end webhook the way Stripe
    does (see paddle_billing.py's module docstring) - this is our replacement: checked
    every tick (cheap, a handful of rows at most - see database.businesses_needing_
    trial_warning), emailing anyone whose card-backed Paddle trial is about to convert to
    a real charge. trial_warning_sent_at dedupes it so a business is only ever warned
    once, marked right after the attempt (success or failure) so a permanently-bad email
    address can't get retried forever - same trade-off appointment reminders above make."""
    for business_id, name, contact_email, trial_ends_at in businesses_needing_trial_warning():
        try:
            send_email(
                recipient=contact_email,
                subject="Your Camilius trial is ending soon",
                body=_trial_ending_email_body(name, trial_ends_at),
                business_name="Camilius",
                reply_to=os.getenv("ALERT_EMAIL") or contact_email,
            )
            print(f"[reminders] Sent trial-ending warning to business_id={business_id}", flush=True)
        except Exception as error:
            print(f"[reminders] Failed to send trial-ending warning for business_id={business_id}: {error}", flush=True)
            log_client_error(business_id, f"trial warning email failed: {error}", "", "trial-warning-debug", "")
        mark_trial_warning_sent(business_id)
