"""Scheduled email/notification jobs run from process_emails.py's background loop
rather than inline with request handling, since neither has anything to do with any
single incoming request:

- Appointment reminders (Faza 9/12, Pro plan) - an owner-facing in-app notification +
  push, sent a configurable lead time before the appointment
  (send_appointment_reminders_if_needed).
- Trial-ending warning emails - see send_trial_ending_warnings_if_needed below.
"""
import os
from datetime import timedelta

from database import (
    appointments_needing_reminders, mark_reminder_sent, business_by_id,
    is_owner_reminders_enabled, get_owner_reminder_minutes,
    has_pro_access, log_client_error, create_notification,
    businesses_needing_trial_warning, mark_trial_warning_sent,
    expire_stale_pending_conversations,
)
from booking import DEFAULT_TZ, tz_for, now_local, relative_when
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


def _owner_reminder_notification_body(customer_name, service, start_local, minutes, tz=DEFAULT_TZ):
    when = relative_when(start_local, tz)
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
        (appointment_id, business_id, customer_name, _sender_igsid, service, start_time,
         _reminder_customer_sent_at, reminder_owner_sent_at) = row

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
        # tzinfo representation, so start_time (whatever offset the DB driver returned it
        # in) minus now needs no extra conversion to be correct - which is also why `now`
        # above can stay a single value rather than being recomputed per business.
        # start_local does need each business's own zone, though: it's what the reminder
        # text actually shows the customer, and "tomorrow at 09:00" is only true in the
        # business's own timezone.
        tz = tz_for(business)
        time_until = start_time - now
        start_local = start_time.astimezone(tz)
        owner_minutes = get_owner_reminder_minutes(business_id)

        if (reminder_owner_sent_at is None and is_owner_reminders_enabled(business_id)
                and abs(time_until - timedelta(minutes=owner_minutes)) <= REMINDER_TOLERANCE):
            title = "Upcoming appointment reminder"
            body = _owner_reminder_notification_body(customer_name, service, start_local, owner_minutes, tz)
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
        f"file to keep your AI page and chat running without interruption.\n\n"
        f"Nothing to do if you want to continue - it's automatic. If you'd rather not "
        f"continue, cancel any time before then using the \"Manage subscription\" link "
        f"in the confirmation email Paddle sent you, or by replying to this email, "
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


def expire_stale_pending_conversations_if_needed():
    """Checked every tick (cheap - a single indexed-by-status UPDATE, matching the other
    every-tick jobs above): sweeps up any conversation that's been sitting 'pending' for
    over 24h and auto-archives it. See database.expire_stale_pending_conversations for
    the actual query."""
    count = expire_stale_pending_conversations()
    if count:
        print(f"[reminders] Auto-archived {count} stale pending conversation(s)", flush=True)
