"""Smart Booking / Schedule (Faza 8) - slot generation, business-hours parsing, and
availability logic for the Schedule page and the AI's chat booking tool (see
generate_reply.py's generate_reply_with_booking). All actual reads/writes go through
database.py; this module owns the calendar math on top of it.

Appointment times are resolved in each business's OWN timezone, passed in as `tz` by
every caller that has a business in hand (see tz_for()). This used to be a single fixed
module-level ZoneInfo("Europe/Belgrade") back when the product was Serbia-only; it is a
parameter now because a business in another country would otherwise have every slot,
every "is this in the past" check, and every "tomorrow at 14:00" resolved against
Belgrade's clock. DEFAULT_TZ remains only as the fallback for a business row that
predates the timezone column, and as the column's own default.
"""
import re
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database import (
    appointments_in_range, create_appointment, cancel_appointment,
    next_upcoming_appointment_for_sender, create_notification,
    is_new_booking_notifications_enabled,
)
from push_notifications import send_push_to_business

DEFAULT_TZ = ZoneInfo("Europe/Belgrade")

# Index 21 of the business row (see database.py's canonical row-shape comment). This is
# deliberately the ONLY place that index is read: the row is indexed positionally in
# well over a hundred places, and concentrating this one read means a future column
# reshuffle breaks in a single spot instead of silently booking everyone in the wrong
# country. Falls back rather than raising - a business row from before the column
# existed, or with a name no longer in the tz database, must still be able to book.
_BUSINESS_TIMEZONE_INDEX = 21


def tz_for(business):
    """The ZoneInfo for a business row from database.business_by_id() and friends."""
    name = business[_BUSINESS_TIMEZONE_INDEX] if len(business) > _BUSINESS_TIMEZONE_INDEX else None
    if not name:
        return DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"[booking] Unknown timezone {name!r} on business row - falling back to {DEFAULT_TZ}", flush=True)
        return DEFAULT_TZ


_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def now_local(tz=DEFAULT_TZ):
    return datetime.now(tz)


def relative_when(start_local, tz=DEFAULT_TZ):
    """"at HH:MM" for a same-day appointment (no date - it's implied by "today" already
    being the day this is about), "tomorrow at HH:MM" for the day after (relative to the
    business's own local "now", not the server's, in case they ever differ), and a full
    weekday/date for anything further out, where neither "today" nor "tomorrow" would be
    accurate. Shared by reminders.py's reminder texts and the new-booking notification
    below - any message that needs to say when an appointment is."""
    time_str = start_local.strftime("%H:%M")
    delta_days = (start_local.date() - now_local(tz).date()).days
    if delta_days == 0:
        return f"at {time_str}"
    if delta_days == 1:
        return f"tomorrow at {time_str}"
    return f"{start_local.strftime('%A, %B %d')} at {time_str}"


# Any dash-like character someone might type (or a phone keyboard's "smart punctuation"
# might substitute for a plain hyphen) between an open and close time - en dash, em dash,
# figure dash, minus sign. Normalized to "-" before the time-range regex runs below.
_DASH_VARIANTS = re.compile("[‐-―−]")  # hyphen..horizontal bar, minus sign


def parse_working_hours(text):
    """Parses the "Monday: 09:00-18:00" / "Sunday: closed" format produced by the
    Profile page's hours picker (static/pickers.js's createHoursPicker) into
    {weekday_int(0=Mon..6=Sun): (open_time, close_time) or None if closed/unparseable}.
    Best-effort, same spirit as the JS picker's own parse() - a business whose working
    hours predate the picker (free-typed text in an unexpected format) just ends up with
    no slots that day rather than erroring, surfaced to the owner on the Schedule page.

    The picker itself only ever emits "HH:MM-HH:MM" with a plain hyphen, but hand-edited
    or pre-picker text is more forgiving in practice - a phone keyboard's autocorrect
    readily swaps "-" for an en dash, and it's common to write hours without minutes
    ("9-18") or with a trailing "h" ("9h-18h"). One day silently parsing to "closed"
    while the neighboring days (typed the same way, same session) parse fine reads as a
    baffling bug to the business owner, not a formatting slip - so all of these are
    accepted rather than just the picker's own exact output."""
    hours = {i: None for i in range(7)}
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        day_part, _, rest = line.partition(":")
        day_part = day_part.strip().lower()
        if day_part not in _WEEKDAY_NAMES:
            continue
        weekday = _WEEKDAY_NAMES.index(day_part)
        rest = _DASH_VARIANTS.sub("-", rest.strip())
        if "closed" in rest.lower():
            continue
        match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*h?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*h?", rest, re.IGNORECASE)
        if not match:
            continue
        oh, ch = int(match.group(1)), int(match.group(3))
        om, cm = int(match.group(2) or 0), int(match.group(4) or 0)
        if 0 <= oh <= 23 and 0 <= om <= 59 and 0 <= ch <= 23 and 0 <= cm <= 59:
            open_t, close_t = dtime(oh, om), dtime(ch, cm)
            if open_t < close_t:
                hours[weekday] = (open_t, close_t)
    return hours


def is_open_on(working_hours_text, local_date):
    return parse_working_hours(working_hours_text).get(local_date.weekday()) is not None


def day_slots(working_hours_text, slot_minutes, local_date, tz=DEFAULT_TZ):
    """Every slot start time (aware, in the business's tz) within business hours on
    local_date, whether free or already booked - the fixed grid every booking (manual or
    AI) is aligned to."""
    open_close = parse_working_hours(working_hours_text).get(local_date.weekday())
    if not open_close:
        return []
    open_t, close_t = open_close
    cursor = datetime.combine(local_date, open_t, tzinfo=tz)
    day_end = datetime.combine(local_date, close_t, tzinfo=tz)
    delta = timedelta(minutes=slot_minutes)
    slots = []
    while cursor + delta <= day_end:
        slots.append(cursor)
        cursor += delta
    return slots


def local_day_bounds(local_date, tz=DEFAULT_TZ):
    start = datetime.combine(local_date, dtime.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def appointments_for_local_day(business_id, local_date, tz=DEFAULT_TZ):
    """Confirmed appointments on local_date, as dicts with start/end in the business's
    own timezone - used by both the Schedule page's day/week views."""
    start, end = local_day_bounds(local_date, tz)
    rows = appointments_in_range(business_id, start, end)
    result = []
    for (appt_id, customer_name, sender_igsid, service, start_time, end_time, status, source, notes) in rows:
        result.append({
            "id": appt_id,
            "customer_name": customer_name,
            "service": service,
            "source": source,
            "start_local": start_time.astimezone(tz),
            "end_local": end_time.astimezone(tz),
        })
    return result


def appointments_by_local_day(business_id, start_date, end_date, tz=DEFAULT_TZ):
    """Confirmed appointments across [start_date, end_date) as {local_date: [dicts]} -
    the week view's version of appointments_for_local_day. One query covering the whole
    range instead of one round trip per day (used to be a 7-query loop for a week)."""
    start, _ = local_day_bounds(start_date, tz)
    _, end = local_day_bounds(end_date - timedelta(days=1), tz)
    rows = appointments_in_range(business_id, start, end)
    result = {}
    for (appt_id, customer_name, sender_igsid, service, start_time, end_time, status, source, notes) in rows:
        start_local = start_time.astimezone(tz)
        result.setdefault(start_local.date(), []).append({
            "id": appt_id,
            "customer_name": customer_name,
            "service": service,
            "source": source,
            "start_local": start_local,
            "end_local": end_time.astimezone(tz),
        })
    return result


def is_slot_free(business_id, start_dt, end_dt):
    return len(appointments_in_range(business_id, start_dt, end_dt)) == 0


def free_slots_for_day(business_id, working_hours_text, slot_minutes, local_date, tz=DEFAULT_TZ):
    slots = day_slots(working_hours_text, slot_minutes, local_date, tz)
    if not slots:
        return []
    booked_rows = appointments_in_range(business_id, slots[0], slots[-1] + timedelta(minutes=slot_minutes))
    booked_ranges = [(row[4], row[5]) for row in booked_rows]
    free = []
    for s in slots:
        e = s + timedelta(minutes=slot_minutes)
        if not any(s < b_end and e > b_start for (b_start, b_end) in booked_ranges):
            free.append(s)
    return free


def find_next_free_slots(business_id, working_hours_text, slot_minutes, after_dt, count=2, search_days=21, tz=DEFAULT_TZ):
    """The next `count` free slot start times at/after after_dt, searching forward day by
    day - used both by the AI's booking tool (offer the next 2 openings when the
    requested time is taken) and could be reused anywhere else "what's free next" is
    needed. search_days caps the search so a business with no open days configured
    (unparseable working hours) can't spin forever."""
    results = []
    d = after_dt.astimezone(tz).date()
    for _ in range(search_days):
        for s in free_slots_for_day(business_id, working_hours_text, slot_minutes, d, tz):
            if s >= after_dt:
                results.append(s)
                if len(results) >= count:
                    return results
        d += timedelta(days=1)
    return results


def slot_within_business_hours(working_hours_text, start_dt, end_dt, tz=DEFAULT_TZ):
    local_date = start_dt.astimezone(tz).date()
    open_close = parse_working_hours(working_hours_text).get(local_date.weekday())
    if not open_close:
        return False
    open_t, close_t = open_close
    day_start = datetime.combine(local_date, open_t, tzinfo=tz)
    day_end = datetime.combine(local_date, close_t, tzinfo=tz)
    return start_dt >= day_start and end_dt <= day_end


def parse_upsell_pairs(text):
    """Parses "Service A -> Service B" (one pair per line, free text set by the owner on
    the Profile page) into a bidirectional {service_name_lower: paired_service_display}
    map - booking either service suggests the other. Best-effort/case-insensitive, same
    "leave what doesn't parse alone" spirit as parse_working_hours."""
    pairs = {}
    for line in (text or "").split("\n"):
        line = line.strip()
        if "->" not in line:
            continue
        left, _, right = line.partition("->")
        left, right = left.strip(), right.strip()
        if left and right:
            pairs[left.lower()] = right
            pairs[right.lower()] = left
    return pairs


def upsell_suggestion_for(upsell_pairs_text, service):
    """The complementary service to gently suggest after booking `service`, if the
    business has defined one - None if not set up, service is blank, or no pair matches."""
    if not service:
        return None
    return parse_upsell_pairs(upsell_pairs_text).get(service.strip().lower())


def notify_owner_of_new_booking(business_id, customer_name, service, start_local, tz=DEFAULT_TZ):
    """In-app notification (+ best-effort push) telling the owner a customer just booked
    themselves in - separate from, and not gated by, the "Remind me"
    reminder toggle (reminders.py) since this fires once at booking time regardless of
    whether owner reminders are even turned on. Gated by its own "New bookings" toggle
    (new_booking_notifications_enabled). Never allowed to fail the booking itself if
    push/notification creation errors - the appointment is already committed by the time
    this runs."""
    if not is_new_booking_notifications_enabled(business_id):
        return
    title = "New appointment booked"
    when = relative_when(start_local, tz)
    if service:
        body = f"{customer_name} booked {service} for {when}."
    else:
        body = f"{customer_name} booked an appointment for {when}."
    try:
        create_notification(business_id, title, body, kind="new_booking")
        send_push_to_business(business_id, title, body, data={"type": "new_booking"})
    except Exception:
        pass


def attempt_ai_booking(business, business_id, slot_minutes, requested_dt_str, customer_name, sender_igsid, service=None, upsell_pairs_text=None):
    """Tool executor for generate_reply.py's check_and_book_appointment tool - the single
    source of truth for what the AI is allowed to tell a customer, since the model only
    ever sees this function's return value and never touches the database itself.

    business = a business_by_id()-shaped tuple (index 8 is working_hours, and tz_for()
    reads the timezone off it - the model's "tomorrow at 14:00" means 14:00 where the
    business is, not where the server is)."""
    working_hours_text = business[8]
    tz = tz_for(business)
    try:
        requested = datetime.fromisoformat(requested_dt_str)
    except (ValueError, TypeError):
        return {"status": "error", "message": "Could not understand that date/time - ask the customer for a clear date and time and try again."}

    requested = requested.replace(tzinfo=tz) if requested.tzinfo is None else requested.astimezone(tz)

    if requested < now_local(tz):
        # Already in the past (customer/model confusion, e.g. a weekday that already
        # passed this week) - treat exactly like any other unavailable time.
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, now_local(tz), count=2, tz=tz)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    end = requested + timedelta(minutes=slot_minutes)

    if not slot_within_business_hours(working_hours_text, requested, end, tz) or not is_slot_free(business_id, requested, end):
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, requested, count=2, tz=tz)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    create_appointment(business_id, customer_name, requested, end, sender_igsid=sender_igsid, service=service, source="ai")
    notify_owner_of_new_booking(business_id, customer_name, service, requested, tz)
    result = {
        "status": "booked",
        "start_time": requested.strftime("%A %Y-%m-%d %H:%M"),
        "end_time": end.strftime("%H:%M"),
    }
    suggestion = upsell_suggestion_for(upsell_pairs_text, service)
    if suggestion:
        result["upsell_suggestion"] = suggestion
    return result


def attempt_ai_cancel_or_reschedule(business, business_id, slot_minutes, sender_igsid, new_requested_datetime=None, upsell_pairs_text=None):
    """Tool executor for generate_reply.py's cancel_or_reschedule_appointment tool. Looks
    up the customer's own next upcoming appointment by sender_igsid (the model never has
    an appointment ID - the chat session it's already in IS the identifier). A
    reschedule verifies the NEW slot works BEFORE canceling the old one, so a failed
    reschedule never leaves the customer with nothing booked."""
    tz = tz_for(business)
    existing = next_upcoming_appointment_for_sender(business_id, sender_igsid)
    if existing is None:
        return {"status": "not_found"}
    existing_id, existing_customer_name, existing_service, existing_start, existing_end = existing

    if not new_requested_datetime:
        cancel_appointment(existing_id)
        return {"status": "canceled", "canceled_start_time": existing_start.astimezone(tz).strftime("%A %Y-%m-%d %H:%M")}

    working_hours_text = business[8]
    try:
        requested = datetime.fromisoformat(new_requested_datetime)
    except (ValueError, TypeError):
        return {"status": "error", "message": "Could not understand that date/time - ask the customer for a clear date and time and try again."}
    requested = requested.replace(tzinfo=tz) if requested.tzinfo is None else requested.astimezone(tz)
    new_end = requested + timedelta(minutes=slot_minutes)

    # The customer's own about-to-be-canceled appointment would otherwise look like a
    # conflict against itself (e.g. shifting a booking 15 minutes later overlaps the old
    # slot) - explicitly excluded from the availability check for that reason.
    overlaps_own_existing_slot = requested < existing_end.astimezone(tz) and new_end > existing_start.astimezone(tz)
    is_available = (
        requested >= now_local(tz)
        and slot_within_business_hours(working_hours_text, requested, new_end, tz)
        and (is_slot_free(business_id, requested, new_end) or overlaps_own_existing_slot)
    )
    if not is_available:
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, max(requested, now_local(tz)), count=2, tz=tz)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    cancel_appointment(existing_id)
    create_appointment(business_id, existing_customer_name, requested, new_end, sender_igsid=sender_igsid, service=existing_service, source="ai")
    result = {
        "status": "rescheduled",
        "old_start_time": existing_start.astimezone(tz).strftime("%A %Y-%m-%d %H:%M"),
        "new_start_time": requested.strftime("%A %Y-%m-%d %H:%M"),
        "new_end_time": new_end.strftime("%H:%M"),
    }
    suggestion = upsell_suggestion_for(upsell_pairs_text, existing_service)
    if suggestion:
        result["upsell_suggestion"] = suggestion
    return result
