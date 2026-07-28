"""Pure-function tests for booking.py's slot/hours/upsell logic - none of these touch
the database (day_slots/parse_working_hours/parse_upsell_pairs are pure calendar math),
so they're fast and don't depend on the appointments table having any particular state."""
from datetime import date, datetime, timedelta

import booking

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
    tz = booking.BUSINESS_TZ
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
