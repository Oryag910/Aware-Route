from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import (
    RATE_LIMIT_DETAIL,
    RATE_LIMIT_GLOBAL_PER_DAY,
    RATE_LIMIT_PER_IP_PER_HOUR,
    RateLimiter,
    rate_limit_dependency,
)


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def test_per_ip_window_allows_up_to_limit() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR):
        limiter.check_and_record("1.2.3.4")


def test_per_ip_window_blocks_over_limit() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR):
        limiter.check_and_record("1.2.3.4")

    with pytest.raises(HTTPException) as exc_info:
        limiter.check_and_record("1.2.3.4")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == RATE_LIMIT_DETAIL


def test_per_ip_window_slides_after_advancing_past_window() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR):
        limiter.check_and_record("1.2.3.4")

    with pytest.raises(HTTPException):
        limiter.check_and_record("1.2.3.4")

    clock.advance(timedelta(hours=1, seconds=1))

    # The old timestamps are now outside the trailing 1-hour window,
    # so a fresh batch up to the limit is allowed again.
    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR):
        limiter.check_and_record("1.2.3.4")


def test_per_ip_window_is_independent_per_ip() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR):
        limiter.check_and_record("1.1.1.1")

    # A different IP has its own independent window.
    limiter.check_and_record("2.2.2.2")


def test_global_daily_cap_blocks_the_request_that_crosses_it() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    # Spread requests across distinct IPs so the per-IP cap (10/hour)
    # never trips before the global cap (90/day) does.
    for index in range(RATE_LIMIT_GLOBAL_PER_DAY):
        limiter.check_and_record(f"10.0.0.{index}")

    with pytest.raises(HTTPException) as exc_info:
        limiter.check_and_record("10.0.1.0")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == RATE_LIMIT_DETAIL


def test_global_daily_cap_slides_after_advancing_past_window() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0))
    limiter = RateLimiter(now=clock)

    for index in range(RATE_LIMIT_GLOBAL_PER_DAY):
        limiter.check_and_record(f"10.0.0.{index}")

    with pytest.raises(HTTPException):
        limiter.check_and_record("10.0.1.0")

    clock.advance(timedelta(days=1, seconds=1))

    # All prior timestamps have aged out of the trailing 24h window.
    limiter.check_and_record("10.0.1.0")


def test_health_endpoint_is_never_rate_limited() -> None:
    for _ in range(RATE_LIMIT_PER_IP_PER_HOUR + 5):
        response = client.get("/health")
        assert response.status_code == 200


def test_routes_with_restroom_endpoint_returns_429_when_limiter_is_over_limit() -> None:  # noqa: E501
    def always_over_limit() -> None:
        raise HTTPException(
            status_code=429,
            detail=RATE_LIMIT_DETAIL,
        )

    app.dependency_overrides[rate_limit_dependency] = (
        always_over_limit
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
        },
    )

    assert response.status_code == 429
    assert response.json() == {"detail": RATE_LIMIT_DETAIL}
