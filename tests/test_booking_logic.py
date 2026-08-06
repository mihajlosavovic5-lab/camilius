"""Pure-function tests for booking.py's slot/hours/upsell logic - none of these touch
the database (day_slots/parse_working_hours/parse_upsell_pairs are pure calendar math),
so they're fast and don't depend on the appointments table having any particular state."""
import json
from datetime import date, datetime, timedelta

import booking

# The pre-JSON "Monday: 09:00-18:00" / "Sunday: closed" free-text format - every business
# saved before the picker switched to JSON still has this stored, so parse_working_hours
# must keep reading it indefinitely (see _parse_working_hours_legacy_text), not just
# during some transition window. Deliberately still used as the default across this file
# rather than switching it to JSON, so this suite keeps exercising that fallback path.
WORKING_HOURS = (
    "Monday: 09:00-18:00\n"
    "Tuesday: 09:00-18:00\n"
    "Wednesday: 09:00-18:00\n"
    "Thursday: 09:00-18:00\n"
    "Friday: 09:00-18:00\n"
    "Saturday: 09:00-14:00\n"
    "Sunday: closed"
)


def test_parse_working_hours_reads_open_and_closed_days():
    hours = booking.parse_working_hours(WORKING_HOURS)
    assert hours[0] == (booking.dtime(9, 0), booking.dtime(18, 0))  # Monday
    assert hours[5] == (booking.dtime(9, 0), booking.dtime(14, 0))  # Saturday
    assert hours[6] is None  # Sunday


def test_parse_working_hours_ignores_unparseable_lines():
    # A day missing entirely, or with garbage instead of a time range, is treated as
    # closed rather than raising - same "best effort" spirit as the JS picker's parse().
    hours = booking.parse_working_hours("Monday: whenever we feel like it\nTuesday: 09:00-18:00")
    assert hours[0] is None
    assert hours[1] == (booking.dtime(9, 0), booking.dtime(18, 0))


def test_parse_working_hours_rejects_inverted_range():
    # close before open would generate a negative-length day - discarded, not a slot generator that loops forever.
    hours = booking.parse_working_hours("Monday: 18:00-09:00")
    assert hours[0] is None


def test_parse_working_hours_tolerates_common_hand_typed_variants():
    # Hand-edited (or pre-picker) text is far messier than the picker's own exact
    # "HH:MM-HH:MM" output - a phone keyboard's autocorrect swaps "-" for an en dash,
    # and it's common to drop the minutes or add a trailing "h". A day silently going
    # "closed" just because it was typed slightly differently than its neighbors is
    # exactly the confusing bug this guards against.
    hours = booking.parse_working_hours(
        "Monday: 09:00–18:00\n"  # en dash instead of a hyphen
        "Tuesday: 9-18\n"  # no minutes
        "Wednesday: 9h-18h\n"  # trailing "h"
    )
    assert hours[0] == (booking.dtime(9, 0), booking.dtime(18, 0))
    assert hours[1] == (booking.dtime(9, 0), booking.dtime(18, 0))
    assert hours[2] == (booking.dtime(9, 0), booking.dtime(18, 0))


def test_parse_working_hours_reads_json_format():
    # The format static/hours-picker.js's shared picker core (and page_generator's
    # format_working_hours) write today - see _parse_working_hours_json.
    text = json.dumps({
        "monday": {"open": "09:00", "close": "18:00"},
        "tuesday": None,
        "saturday": {"open": "09:00", "close": "14:00"},
    })
    hours = booking.parse_working_hours(text)
    assert hours[0] == (booking.dtime(9, 0), booking.dtime(18, 0))  # Monday
    assert hours[1] is None  # Tuesday
    assert hours[5] == (booking.dtime(9, 0), booking.dtime(14, 0))  # Saturday
    assert hours[6] is None  # Sunday - absent from the dict entirely


def test_parse_working_hours_json_rejects_inverted_or_malformed_day():
    # Same "closed rather than erroring" spirit as the legacy parser's own tests -
    # a nonsensical or unparseable day is simply absent from the result, not raised.
    text = json.dumps({
        "monday": {"open": "18:00", "close": "09:00"},  # inverted
        "tuesday": {"open": "not-a-time", "close": "18:00"},
        "wednesday": {"open": "09:00"},  # missing close
    })
    hours = booking.parse_working_hours(text)
    assert hours[0] is None
    assert hours[1] is None
    assert hours[2] is None


def test_parse_working_hours_json_takes_priority_over_legacy_text():
    # A malformed/non-JSON string (including plain legacy text, which never starts with
    # "{") falls through to the legacy parser - proven by WORKING_HOURS itself parsing
    # correctly elsewhere in this file. This just proves the JSON branch is tried first
    # and wins when the text does look like JSON.
    assert booking.parse_working_hours("{not valid json") == {i: None for i in range(7)}


def test_working_hours_display_text_reads_either_format():
    json_text = json.dumps({"monday": {"open": "09:00", "close": "18:00"}, "sunday": None})
    legacy_text = "Monday: 09:00-18:00\nSunday: closed"
    assert booking.working_hours_display_text(json_text).splitlines()[0] == "Monday: 09:00-18:00"
    assert booking.working_hours_display_text(legacy_text).splitlines()[0] == "Monday: 09:00-18:00"
    assert booking.working_hours_display_text(json_text).splitlines()[6] == "Sunday: closed"


def test_day_slots_covers_the_full_open_window():
    monday = date(2026, 7, 27)  # a real Monday
    slots = booking.day_slots(WORKING_HOURS, 30, monday)
    assert len(slots) == 18  # 09:00-18:00 in 30-minute steps
    assert slots[0].strftime("%H:%M") == "09:00"
    assert slots[-1].strftime("%H:%M") == "17:30"


def test_day_slots_empty_on_a_closed_day():
    sunday = date(2026, 8, 2)
    assert booking.day_slots(WORKING_HOURS, 30, sunday) == []


def test_day_slots_respects_a_different_slot_duration():
    monday = date(2026, 7, 27)
    slots = booking.day_slots(WORKING_HOURS, 60, monday)
    assert len(slots) == 9  # 09:00-18:00 in 60-minute steps


def test_slot_within_business_hours():
    monday = date(2026, 7, 27)
    tz = booking.DEFAULT_TZ
    start = datetime.combine(monday, booking.dtime(10, 0), tzinfo=tz)
    end = start + timedelta(minutes=30)
    assert booking.slot_within_business_hours(WORKING_HOURS, start, end) is True

    after_close = datetime.combine(monday, booking.dtime(19, 0), tzinfo=tz)
    assert booking.slot_within_business_hours(WORKING_HOURS, after_close, after_close + timedelta(minutes=30)) is False


def test_is_open_on():
    assert booking.is_open_on(WORKING_HOURS, date(2026, 7, 27)) is True  # Monday
    assert booking.is_open_on(WORKING_HOURS, date(2026, 8, 2)) is False  # Sunday


def test_parse_upsell_pairs_is_bidirectional_and_case_insensitive():
    pairs = booking.parse_upsell_pairs("Haircut -> Treatment\nManicure -> Pedicure")
    assert pairs["haircut"] == "Treatment"
    assert pairs["treatment"] == "Haircut"
    assert pairs["manicure"] == "Pedicure"


def test_upsell_suggestion_for():
    text = "Haircut -> Treatment"
    assert booking.upsell_suggestion_for(text, "Haircut") == "Treatment"
    assert booking.upsell_suggestion_for(text, "haircut") == "Treatment"  # case-insensitive
    assert booking.upsell_suggestion_for(text, "Manicure") is None
    assert booking.upsell_suggestion_for(None, "Haircut") is None
    assert booking.upsell_suggestion_for(text, "") is None


def test_now_local_is_timezone_aware_in_business_tz():
    now = booking.now_local()
    assert now.tzinfo is not None
    assert str(now.tzinfo) == "Europe/Belgrade"
