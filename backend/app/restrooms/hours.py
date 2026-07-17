import re
from dataclasses import dataclass
from datetime import datetime

from app.restrooms.models import Restroom


MINUTES_PER_DAY = 24 * 60

# "dawn to dusk" / "sunrise to sunset" have no fixed clock time -- this
# is a deliberate, documented approximation (not a parsed value) since
# most US parks with that phrasing are open well before 8 AM in summer
# and closed well before 8 PM in winter. Good enough for a coarse
# "confidently closed" filter, not precise enough for anything finer.
DAWN_TO_DUSK_OPEN_MINUTE = 6 * 60
DAWN_TO_DUSK_CLOSE_MINUTE = 20 * 60

_ALWAYS_OPEN_PATTERN = re.compile(
    r"^(open\s+)?24\s*(hours|/\s*7|hrs)?$"
)
_DAWN_TO_DUSK_PATTERN = re.compile(
    r"^(dawn\s+to\s+dusk|sunrise\s+to\s+sunset)$"
)
_TIME_RANGE_PATTERN = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)"
    r"\s*-\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)$"
)


@dataclass(frozen=True)
class DailyHours:
    open_minute: int
    close_minute: int


def _clock_to_minute(hour: int, minute: int, meridiem: str) -> int:
    hour = hour % 12

    if meridiem == "pm":
        hour += 12

    return hour * 60 + minute


def parse_hours(text: str | None) -> DailyHours | None:
    """Parse a single daily open/close range out of free-text hours.

    Returns None for missing or unparseable text -- None means
    UNKNOWN, never "closed". Multi-day schedules, "varies", seasonal
    notes, and anything else not explicitly supported below all fall
    through to None rather than guessing.
    """
    if text is None:
        return None

    normalized = " ".join(text.strip().lower().split())

    if not normalized:
        return None

    if _ALWAYS_OPEN_PATTERN.match(normalized):
        return DailyHours(open_minute=0, close_minute=MINUTES_PER_DAY)

    if _DAWN_TO_DUSK_PATTERN.match(normalized):
        return DailyHours(
            open_minute=DAWN_TO_DUSK_OPEN_MINUTE,
            close_minute=DAWN_TO_DUSK_CLOSE_MINUTE,
        )

    match = _TIME_RANGE_PATTERN.match(normalized)

    if match is None:
        return None

    (
        open_hour,
        open_minute,
        open_meridiem,
        close_hour,
        close_minute,
        close_meridiem,
    ) = match.groups()

    open_total = _clock_to_minute(
        int(open_hour),
        int(open_minute or 0),
        open_meridiem,
    )
    close_total = _clock_to_minute(
        int(close_hour),
        int(close_minute or 0),
        close_meridiem,
    )

    return DailyHours(open_minute=open_total, close_minute=close_total)


def is_open_at(hours: DailyHours, at: datetime) -> bool:
    """Open-minute is inclusive, close-minute is exclusive. Handles
    overnight wrap (close_minute < open_minute) by treating the range
    as spanning midnight."""
    minute_of_day = at.hour * 60 + at.minute

    if hours.close_minute < hours.open_minute:
        return (
            minute_of_day >= hours.open_minute
            or minute_of_day < hours.close_minute
        )

    return hours.open_minute <= minute_of_day < hours.close_minute


def confidently_closed(restroom: Restroom, at: datetime) -> bool:
    """True only when hours are parseable AND `at` falls outside them.
    Unknown/unparseable hours always return False -- the product must
    never claim a restroom is closed when it doesn't actually know."""
    hours = parse_hours(restroom.hours_of_operation)

    if hours is None:
        return False

    return not is_open_at(hours, at)
