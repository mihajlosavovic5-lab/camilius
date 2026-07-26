"""Smart Booking / Schedule (Faza 8) - slot generation, business-hours parsing, and
availability logic for the Schedule page and the AI's Instagram-DM booking tool (see
generate_reply.py's generate_reply_with_booking). All actual reads/writes go through
database.py; this module owns the calendar math on top of it.

Camilius is a single-market (Serbia) product as of this writing, so appointment times
use one fixed business timezone rather than a per-business setting - see BUSINESS_TZ.
"""
import re
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from database import appointments_in_range, create_appointment, cancel_appointment, next_upcoming_appointment_for_sender

BUSINESS_TZ = ZoneInfo("Europe/Belgrade")

_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def now_local():
    return datetime.now(BUSINESS_TZ)


def parse_working_hours(text):
    """Parses the "Monday: 09:00-18:00" / "Sunday: closed" format produced by the
    Profile page's hours picker (static/pickers.js's createHoursPicker) into
    {weekday_int(0=Mon..6=Sun): (open_time, close_time) or None if closed/unparseable}.
    Best-effort, same spirit as the JS picker's own parse() - a business whose working
    hours predate the picker (free-typed text in an unexpected format) just ends up with
    no slots that day rather than erroring, surfaced to the owner on the Schedule page."""
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
        rest = rest.strip()
        if "closed" in rest.lower():
            continue
        match = re.match(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", rest)
        if not match:
            continue
        oh, om, ch, cm = (int(x) for x in match.groups())
        if 0 <= oh <= 23 and 0 <= om <= 59 and 0 <= ch <= 23 and 0 <= cm <= 59:
            open_t, close_t = dtime(oh, om), dtime(ch, cm)
            if open_t < close_t:
                hours[weekday] = (open_t, close_t)
    return hours


def is_open_on(working_hours_text, local_date):
    return parse_working_hours(working_hours_text).get(local_date.weekday()) is not None


def day_slots(working_hours_text, slot_minutes, local_date):
    """Every slot start time (aware, BUSINESS_TZ) within business hours on local_date,
    whether free or already booked - the fixed grid every booking (manual or AI) is
    aligned to."""
    open_close = parse_working_hours(working_hours_text).get(local_date.weekday())
    if not open_close:
        return []
    open_t, close_t = open_close
    cursor = datetime.combine(local_date, open_t, tzinfo=BUSINESS_TZ)
    day_end = datetime.combine(local_date, close_t, tzinfo=BUSINESS_TZ)
    delta = timedelta(minutes=slot_minutes)
    slots = []
    while cursor + delta <= day_end:
        slots.append(cursor)
        cursor += delta
    return slots


def local_day_bounds(local_date):
    start = datetime.combine(local_date, dtime.min, tzinfo=BUSINESS_TZ)
    return start, start + timedelta(days=1)


def appointments_for_local_day(business_id, local_date):
    """Confirmed appointments on local_date, as dicts with BUSINESS_TZ-local start/end -
    used by both the Schedule page's day/week views."""
    start, end = local_day_bounds(local_date)
    rows = appointments_in_range(business_id, start, end)
    result = []
    for (appt_id, customer_name, sender_igsid, service, start_time, end_time, status, source, notes) in rows:
        result.append({
            "id": appt_id,
            "customer_name": customer_name,
            "service": service,
            "source": source,
            "start_local": start_time.astimezone(BUSINESS_TZ),
            "end_local": end_time.astimezone(BUSINESS_TZ),
        })
    return result


def appointments_by_local_day(business_id, start_date, end_date):
    """Confirmed appointments across [start_date, end_date) as {local_date: [dicts]} -
    the week view's version of appointments_for_local_day. One query covering the whole
    range instead of one round trip per day (used to be a 7-query loop for a week)."""
    start, _ = local_day_bounds(start_date)
    _, end = local_day_bounds(end_date - timedelta(days=1))
    rows = appointments_in_range(business_id, start, end)
    result = {}
    for (appt_id, customer_name, sender_igsid, service, start_time, end_time, status, source, notes) in rows:
        start_local = start_time.astimezone(BUSINESS_TZ)
        result.setdefault(start_local.date(), []).append({
            "id": appt_id,
            "customer_name": customer_name,
            "service": service,
            "source": source,
            "start_local": start_local,
            "end_local": end_time.astimezone(BUSINESS_TZ),
        })
    return result


def is_slot_free(business_id, start_dt, end_dt):
    return len(appointments_in_range(business_id, start_dt, end_dt)) == 0


def free_slots_for_day(business_id, working_hours_text, slot_minutes, local_date):
    slots = day_slots(working_hours_text, slot_minutes, local_date)
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


def find_next_free_slots(business_id, working_hours_text, slot_minutes, after_dt, count=2, search_days=21):
    """The next `count` free slot start times at/after after_dt, searching forward day by
    day - used both by the AI's booking tool (offer the next 2 openings when the
    requested time is taken) and could be reused anywhere else "what's free next" is
    needed. search_days caps the search so a business with no open days configured
    (unparseable working hours) can't spin forever."""
    results = []
    d = after_dt.astimezone(BUSINESS_TZ).date()
    for _ in range(search_days):
        for s in free_slots_for_day(business_id, working_hours_text, slot_minutes, d):
            if s >= after_dt:
                results.append(s)
                if len(results) >= count:
                    return results
        d += timedelta(days=1)
    return results


def slot_within_business_hours(working_hours_text, start_dt, end_dt):
    local_date = start_dt.astimezone(BUSINESS_TZ).date()
    open_close = parse_working_hours(working_hours_text).get(local_date.weekday())
    if not open_close:
        return False
    open_t, close_t = open_close
    day_start = datetime.combine(local_date, open_t, tzinfo=BUSINESS_TZ)
    day_end = datetime.combine(local_date, close_t, tzinfo=BUSINESS_TZ)
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


def attempt_ai_booking(business, business_id, slot_minutes, requested_dt_str, customer_name, sender_igsid, service=None, upsell_pairs_text=None):
    """Tool executor for generate_reply.py's check_and_book_appointment tool - the single
    source of truth for what the AI is allowed to tell a customer, since the model only
    ever sees this function's return value and never touches the database itself.

    business = a business_by_id()-shaped tuple (index 8 is working_hours)."""
    working_hours_text = business[8]
    try:
        requested = datetime.fromisoformat(requested_dt_str)
    except (ValueError, TypeError):
        return {"status": "error", "message": "Could not understand that date/time - ask the customer for a clear date and time and try again."}

    requested = requested.replace(tzinfo=BUSINESS_TZ) if requested.tzinfo is None else requested.astimezone(BUSINESS_TZ)

    if requested < now_local():
        # Already in the past (customer/model confusion, e.g. a weekday that already
        # passed this week) - treat exactly like any other unavailable time.
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, now_local(), count=2)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    end = requested + timedelta(minutes=slot_minutes)

    if not slot_within_business_hours(working_hours_text, requested, end) or not is_slot_free(business_id, requested, end):
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, requested, count=2)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    create_appointment(business_id, customer_name, requested, end, sender_igsid=sender_igsid, service=service, source="ai")
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
    an appointment ID - the Instagram thread it's already in IS the identifier). A
    reschedule verifies the NEW slot works BEFORE canceling the old one, so a failed
    reschedule never leaves the customer with nothing booked."""
    existing = next_upcoming_appointment_for_sender(business_id, sender_igsid)
    if existing is None:
        return {"status": "not_found"}
    existing_id, existing_customer_name, existing_service, existing_start, existing_end = existing

    if not new_requested_datetime:
        cancel_appointment(existing_id)
        return {"status": "canceled", "canceled_start_time": existing_start.astimezone(BUSINESS_TZ).strftime("%A %Y-%m-%d %H:%M")}

    working_hours_text = business[8]
    try:
        requested = datetime.fromisoformat(new_requested_datetime)
    except (ValueError, TypeError):
        return {"status": "error", "message": "Could not understand that date/time - ask the customer for a clear date and time and try again."}
    requested = requested.replace(tzinfo=BUSINESS_TZ) if requested.tzinfo is None else requested.astimezone(BUSINESS_TZ)
    new_end = requested + timedelta(minutes=slot_minutes)

    # The customer's own about-to-be-canceled appointment would otherwise look like a
    # conflict against itself (e.g. shifting a booking 15 minutes later overlaps the old
    # slot) - explicitly excluded from the availability check for that reason.
    overlaps_own_existing_slot = requested < existing_end.astimezone(BUSINESS_TZ) and new_end > existing_start.astimezone(BUSINESS_TZ)
    is_available = (
        requested >= now_local()
        and slot_within_business_hours(working_hours_text, requested, new_end)
        and (is_slot_free(business_id, requested, new_end) or overlaps_own_existing_slot)
    )
    if not is_available:
        alternatives = find_next_free_slots(business_id, working_hours_text, slot_minutes, max(requested, now_local()), count=2)
        return {"status": "unavailable", "alternatives": [a.strftime("%A %Y-%m-%d %H:%M") for a in alternatives]}

    cancel_appointment(existing_id)
    create_appointment(business_id, existing_customer_name, requested, new_end, sender_igsid=sender_igsid, service=existing_service, source="ai")
    result = {
        "status": "rescheduled",
        "old_start_time": existing_start.astimezone(BUSINESS_TZ).strftime("%A %Y-%m-%d %H:%M"),
        "new_start_time": requested.strftime("%A %Y-%m-%d %H:%M"),
        "new_end_time": new_end.strftime("%H:%M"),
    }
    suggestion = upsell_suggestion_for(upsell_pairs_text, existing_service)
    if suggestion:
        result["upsell_suggestion"] = suggestion
    return result
