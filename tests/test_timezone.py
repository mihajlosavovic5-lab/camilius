"""Per-business timezone handling.

booking.py used to hold one module-level ZoneInfo("Europe/Belgrade") for the whole
product, which was correct while Camilius was Serbia-only. These tests pin the behavior
that replaced it: the timezone is read off the business row, every slot/availability
calculation resolves against THAT zone, and anything missing or unrecognizable falls
back to DEFAULT_TZ rather than raising - a business must never become unable to book
because of a bad timezone string.

Deliberately no database: these are pure-function tests over booking.py, same style as
test_booking_logic.py.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import booking

WORKING_HOURS = "\n".join([
    "Monday: 09:00-18:00",
    "Tuesday: 09:00-18:00",
    "Wednesday: 09:00-18:00",
    "Thursday: 09:00-18:00",
    "Friday: 09:00-18:00",
    "Saturday: closed",
    "Sunday: closed",
])

# The business row is indexed positionally (see database.py's canonical row-shape
# comment); index 20 is the timezone. Built by length rather than by spelling out 20
# placeholders so this doesn't need editing every time an unrelated column is appended.
def _business_row(timezone_name, working_hours=WORKING_HOURS):
    row = [None] * 21
    row[7] = working_hours
    row[20] = timezone_name
    return tuple(row)


def test_tz_for_reads_the_business_row():
    assert booking.tz_for(_business_row("America/New_York")) == ZoneInfo("America/New_York")
    assert booking.tz_for(_business_row("Asia/Tokyo")) == ZoneInfo("Asia/Tokyo")


def test_tz_for_falls_back_rather_than_raising():
    # NULL/empty column, an unknown zone name, and a row from before the column existed
    # (too short to index) all have to degrade to the default instead of blowing up the
    # booking path.
    assert booking.tz_for(_business_row(None)) == booking.DEFAULT_TZ
    assert booking.tz_for(_business_row("")) == booking.DEFAULT_TZ
    assert booking.tz_for(_business_row("Not/A_Real_Zone")) == booking.DEFAULT_TZ
    assert booking.tz_for(tuple([None] * 20)) == booking.DEFAULT_TZ


def test_day_slots_are_local_to_the_business_timezone():
    """09:00 means 09:00 where the business is - the same wall-clock time in a different
    zone is a different instant, which is the whole point of the column."""
    monday = date(2026, 7, 27)
    belgrade = booking.day_slots(WORKING_HOURS, 60, monday, ZoneInfo("Europe/Belgrade"))
    new_york = booking.day_slots(WORKING_HOURS, 60, monday, ZoneInfo("America/New_York"))

    assert belgrade[0].strftime("%H:%M") == "09:00"
    assert new_york[0].strftime("%H:%M") == "09:00"
    assert len(belgrade) == len(new_york) == 9

    # Summer 2026: Belgrade is UTC+2, New York UTC-4, so the same local 09:00 is 6 hours apart.
    assert (new_york[0] - belgrade[0]) == timedelta(hours=6)
    assert belgrade[0].utcoffset() != new_york[0].utcoffset()


def test_slot_within_business_hours_uses_the_given_timezone():
    monday = date(2026, 7, 27)
    ny = ZoneInfo("America/New_York")

    # 10:00 New York time is inside 09:00-18:00 for a New York business...
    start = datetime.combine(monday, booking.dtime(10, 0), tzinfo=ny)
    assert booking.slot_within_business_hours(WORKING_HOURS, start, start + timedelta(minutes=30), ny) is True

    # ...but that exact same instant is 16:00 in Belgrade and 23:00 in Tokyo, where the
    # business is already closed. Same moment, different verdict, purely from the zone.
    tokyo = ZoneInfo("Asia/Tokyo")
    assert booking.slot_within_business_hours(WORKING_HOURS, start, start + timedelta(minutes=30), tokyo) is False


def test_now_local_defaults_to_default_tz():
    assert booking.now_local().tzinfo == booking.DEFAULT_TZ
    assert booking.now_local(ZoneInfo("Asia/Tokyo")).tzinfo == ZoneInfo("Asia/Tokyo")


def test_relative_when_is_evaluated_against_the_business_clock():
    """"tomorrow at 09:00" has to mean tomorrow for the business, not for the server."""
    tokyo = ZoneInfo("Asia/Tokyo")
    tomorrow_in_tokyo = booking.now_local(tokyo) + timedelta(days=1)
    assert booking.relative_when(tomorrow_in_tokyo, tokyo).startswith("tomorrow at ")

    later_today_in_tokyo = booking.now_local(tokyo).replace(hour=23, minute=0, second=0, microsecond=0)
    if later_today_in_tokyo > booking.now_local(tokyo):
        assert booking.relative_when(later_today_in_tokyo, tokyo).startswith("at ")
