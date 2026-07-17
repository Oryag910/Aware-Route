from datetime import datetime

import pytest

from app.restrooms.hours import (
    DailyHours,
    confidently_closed,
    is_open_at,
    parse_hours,
)
from app.restrooms.models import Restroom


def make_restroom(hours_of_operation: str | None) -> Restroom:
    return Restroom(
        source_id="test",
        facility_name="Test Restroom",
        status="Operational",
        hours_of_operation=hours_of_operation,
        accessibility=None,
        website=None,
        latitude=40.70,
        longitude=-74.00,
    )


def test_parse_hours_none_returns_none() -> None:
    assert parse_hours(None) is None


def test_parse_hours_empty_string_returns_none() -> None:
    assert parse_hours("") is None
    assert parse_hours("   ") is None


def test_parse_hours_unparseable_text_returns_none() -> None:
    assert parse_hours("varies seasonally") is None
    assert parse_hours("Mon-Fri 9-5, Sat 10-2") is None
    assert parse_hours("closed for renovations") is None


def test_parse_hours_standard_range_with_minutes() -> None:
    result = parse_hours("8:00 AM - 8:00 PM")

    assert result == DailyHours(open_minute=8 * 60, close_minute=20 * 60)


def test_parse_hours_range_without_minutes() -> None:
    result = parse_hours("8 AM - 8 PM")

    assert result == DailyHours(open_minute=8 * 60, close_minute=20 * 60)


def test_parse_hours_tight_no_space_lowercase() -> None:
    result = parse_hours("7:30am-6pm")

    assert result == DailyHours(
        open_minute=7 * 60 + 30,
        close_minute=18 * 60,
    )


def test_parse_hours_is_case_insensitive_and_whitespace_tolerant() -> None:  # noqa: E501
    result = parse_hours("  8:00   am  -   8:00   pm  ")

    assert result == DailyHours(open_minute=8 * 60, close_minute=20 * 60)


def test_parse_hours_24_hours_variants() -> None:
    expected = DailyHours(open_minute=0, close_minute=1440)

    assert parse_hours("24 hours") == expected
    assert parse_hours("24/7") == expected
    assert parse_hours("open 24 hours") == expected
    assert parse_hours("Open 24 Hours") == expected


def test_parse_hours_dawn_to_dusk_variants() -> None:
    expected = DailyHours(open_minute=6 * 60, close_minute=20 * 60)

    assert parse_hours("dawn to dusk") == expected
    assert parse_hours("sunrise to sunset") == expected
    assert parse_hours("Dawn to Dusk") == expected


def test_is_open_at_open_minute_is_inclusive() -> None:
    hours = DailyHours(open_minute=8 * 60, close_minute=20 * 60)

    at = datetime(2026, 1, 1, 8, 0)

    assert is_open_at(hours, at) is True


def test_is_open_at_close_minute_is_exclusive() -> None:
    hours = DailyHours(open_minute=8 * 60, close_minute=20 * 60)

    at = datetime(2026, 1, 1, 20, 0)

    assert is_open_at(hours, at) is False


def test_is_open_at_one_minute_before_close_is_open() -> None:
    hours = DailyHours(open_minute=8 * 60, close_minute=20 * 60)

    at = datetime(2026, 1, 1, 19, 59)

    assert is_open_at(hours, at) is True


def test_is_open_at_one_minute_before_open_is_closed() -> None:
    hours = DailyHours(open_minute=8 * 60, close_minute=20 * 60)

    at = datetime(2026, 1, 1, 7, 59)

    assert is_open_at(hours, at) is False


def test_is_open_at_overnight_wrap_inside_late_range() -> None:
    # e.g. "10 PM - 6 AM"
    hours = DailyHours(open_minute=22 * 60, close_minute=6 * 60)

    at = datetime(2026, 1, 1, 23, 0)

    assert is_open_at(hours, at) is True


def test_is_open_at_overnight_wrap_inside_early_range() -> None:
    hours = DailyHours(open_minute=22 * 60, close_minute=6 * 60)

    at = datetime(2026, 1, 1, 3, 0)

    assert is_open_at(hours, at) is True


def test_is_open_at_overnight_wrap_outside_range() -> None:
    hours = DailyHours(open_minute=22 * 60, close_minute=6 * 60)

    at = datetime(2026, 1, 1, 12, 0)

    assert is_open_at(hours, at) is False


def test_is_open_at_overnight_wrap_close_minute_still_exclusive() -> None:
    hours = DailyHours(open_minute=22 * 60, close_minute=6 * 60)

    at = datetime(2026, 1, 1, 6, 0)

    assert is_open_at(hours, at) is False


def test_confidently_closed_unknown_hours_is_never_closed() -> None:
    restroom = make_restroom(None)

    at = datetime(2026, 1, 1, 3, 0)

    assert confidently_closed(restroom, at) is False


def test_confidently_closed_unparseable_hours_is_never_closed() -> None:
    restroom = make_restroom("varies seasonally")

    at = datetime(2026, 1, 1, 3, 0)

    assert confidently_closed(restroom, at) is False


def test_confidently_closed_true_outside_parsed_hours() -> None:
    restroom = make_restroom("8:00 AM - 8:00 PM")

    at = datetime(2026, 1, 1, 23, 0)

    assert confidently_closed(restroom, at) is True


def test_confidently_closed_false_inside_parsed_hours() -> None:
    restroom = make_restroom("8:00 AM - 8:00 PM")

    at = datetime(2026, 1, 1, 12, 0)

    assert confidently_closed(restroom, at) is False


@pytest.mark.parametrize(
    "text",
    [
        "8:00 AM - 8:00 PM",
        "8 AM - 8 PM",
        "7:30am-6pm",
        "24 hours",
        "24/7",
        "dawn to dusk",
        "sunrise to sunset",
    ],
)
def test_parse_hours_supported_formats_all_parse(text: str) -> None:
    assert parse_hours(text) is not None
