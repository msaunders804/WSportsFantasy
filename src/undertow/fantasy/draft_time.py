"""Draft start times: reading what staff type, and showing it to members. Pure, no database.

A time is stored in UTC. Staff enter it in a North American zone (default Eastern, where the PWHL
season runs), and members see it in Eastern with a countdown. The pages also convert it to the
visitor's own zone in the browser.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# The zones staff can enter a time in, with the short label shown next to it.
ZONES = {
    "America/New_York": "ET",
    "America/Chicago": "CT",
    "America/Denver": "MT",
    "America/Los_Angeles": "PT",
    "UTC": "UTC",
}
DEFAULT_ZONE = "America/New_York"


def parse_local(value: str, zone: str = DEFAULT_ZONE) -> datetime | None:
    """Turn a browser `datetime-local` value ("2026-11-30T19:00") into UTC.

    Blank means "no draft time" and gives None. Anything unreadable, or a zone that is not in
    ZONES, raises ValueError.
    """
    value = value.strip()
    if zone not in ZONES:
        raise ValueError("Pick a time zone from the list.")
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Enter the draft time as a date and a time.") from exc
    if local.tzinfo is not None:
        raise ValueError("Enter the draft time as a date and a time.")
    return local.replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)


def format_in_zone(moment: datetime, zone: str = DEFAULT_ZONE) -> str:
    """ "Mon, Nov 30 at 7:00 PM ET" for a UTC moment."""
    local = moment.astimezone(ZoneInfo(zone))
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a, %b} {local.day} at {hour}:{local:%M} {ampm} {ZONES.get(zone, zone)}"


def to_local_input(moment: datetime | None, zone: str = DEFAULT_ZONE) -> str:
    """The `datetime-local` value to pre-fill a form with (empty when there is no time)."""
    if moment is None:
        return ""
    return moment.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M")


@dataclass(frozen=True)
class Countdown:
    text: str  # "in 3 days", "in 5 hours", "in less than an hour", or "" once it has started
    started: bool


def countdown(moment: datetime, now: datetime) -> Countdown:
    seconds = (moment - now).total_seconds()
    if seconds <= 0:
        return Countdown("", True)
    days, rest = divmod(int(seconds), 86400)
    hours = rest // 3600
    if days >= 1:
        return Countdown(f"in {days} day{'s' if days != 1 else ''}", False)
    if hours >= 1:
        return Countdown(f"in {hours} hour{'s' if hours != 1 else ''}", False)
    return Countdown("in less than an hour", False)
