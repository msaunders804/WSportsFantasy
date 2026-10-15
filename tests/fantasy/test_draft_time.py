from datetime import UTC, datetime, timedelta

import pytest

from undertow.fantasy import draft_time as dt


def test_eastern_time_in_winter_is_five_hours_behind_utc():
    assert dt.parse_local("2026-11-30T19:00") == datetime(2026, 12, 1, 0, 0, tzinfo=UTC)


def test_eastern_time_in_summer_is_four_hours_behind_utc():
    assert dt.parse_local("2026-07-01T19:00") == datetime(2026, 7, 1, 23, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("zone", "expected_hour"),
    [
        ("America/New_York", 0),
        ("America/Chicago", 1),
        ("America/Denver", 2),
        ("America/Los_Angeles", 3),
        ("UTC", 19),
    ],
)
def test_each_zone_converts_to_utc(zone, expected_hour):
    got = dt.parse_local("2026-11-30T19:00", zone)
    assert got.hour == expected_hour and got.tzinfo is not None


def test_blank_means_no_time():
    assert dt.parse_local("") is None
    assert dt.parse_local("   ") is None


@pytest.mark.parametrize(
    "value", ["tomorrow", "2026-13-40T10:00", "2026-11-30T19:00Z", "2026-11-30T19:00+05:00"]
)
def test_unreadable_or_zoned_input_is_refused(value):
    with pytest.raises(ValueError):
        dt.parse_local(value)


@pytest.mark.parametrize("zone", ["", "Mars/Olympus", "America/Toronto", "EST"])
def test_only_listed_zones_are_accepted_even_for_a_blank_time(zone):
    with pytest.raises(ValueError, match="time zone"):
        dt.parse_local("", zone)
    with pytest.raises(ValueError, match="time zone"):
        dt.parse_local("2026-11-30T19:00", zone)


def test_seconds_in_the_input_are_fine():
    assert dt.parse_local("2026-11-30T19:00:30").second == 30


def test_format_in_eastern():
    moment = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
    assert dt.format_in_zone(moment) == "Mon, Nov 30 at 7:00 PM ET"
    assert (
        dt.format_in_zone(datetime(2026, 12, 1, 17, 5, tzinfo=UTC)) == "Tue, Dec 1 at 12:05 PM ET"
    )
    assert dt.format_in_zone(datetime(2026, 12, 1, 5, 0, tzinfo=UTC)) == "Tue, Dec 1 at 12:00 AM ET"
    assert dt.format_in_zone(moment, "UTC") == "Tue, Dec 1 at 12:00 AM UTC"


def test_form_prefill_roundtrips():
    moment = dt.parse_local("2026-11-30T19:00")
    assert dt.to_local_input(moment) == "2026-11-30T19:00"
    assert dt.to_local_input(None) == ""
    assert dt.parse_local(dt.to_local_input(moment)) == moment


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("delta", "text", "started"),
    [
        (timedelta(days=29, hours=5), "in 29 days", False),
        (timedelta(days=1, minutes=1), "in 1 day", False),
        (timedelta(hours=23, minutes=59), "in 23 hours", False),
        (timedelta(hours=1), "in 1 hour", False),
        (timedelta(minutes=59), "in less than an hour", False),
        (timedelta(seconds=1), "in less than an hour", False),
        (timedelta(0), "", True),
        (-timedelta(hours=3), "", True),
    ],
)
def test_countdown(delta, text, started):
    result = dt.countdown(NOW + delta, NOW)
    assert (result.text, result.started) == (text, started)
