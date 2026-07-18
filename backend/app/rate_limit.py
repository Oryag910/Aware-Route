from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

# In-memory, single-process rate limiter. Deliberately NOT built for
# horizontal scaling -- state lives in this process's memory, so
# running multiple instances (or restarting the process) resets the
# counters. That's fine at demo scale on a single Render web service;
# it would need a shared store (e.g. Redis) to survive multiple
# instances.

# Sized to stay well under the ORS free tier: each /routes* request
# costs up to 20 upstream ORS calls, so 90 requests/day leaves
# headroom under the ~100 requests/day free tier limit.
RATE_LIMIT_PER_IP_PER_HOUR = 10
RATE_LIMIT_GLOBAL_PER_DAY = 90

RATE_LIMIT_DETAIL = "Demo request limit reached — try again later."


class RateLimiter:
    """Sliding-window limiter, per-IP (trailing 1 hour) and global
    (trailing 24 hours). Both windows slide continuously rather than
    resetting on a calendar boundary, so a burst right at midnight or
    the top of the hour can't double the effective limit."""

    def __init__(
        self,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._now = now
        self._per_ip: dict[str, deque[datetime]] = {}
        self._global: deque[datetime] = deque()

    def _prune(
        self,
        timestamps: deque[datetime],
        window: timedelta,
        current_time: datetime,
    ) -> None:
        cutoff = current_time - window
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

    def check_and_record(self, ip: str) -> None:
        current_time = self._now()

        ip_timestamps = self._per_ip.setdefault(ip, deque())
        self._prune(ip_timestamps, timedelta(hours=1), current_time)
        self._prune(self._global, timedelta(days=1), current_time)

        if len(ip_timestamps) >= RATE_LIMIT_PER_IP_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail=RATE_LIMIT_DETAIL,
            )

        if len(self._global) >= RATE_LIMIT_GLOBAL_PER_DAY:
            raise HTTPException(
                status_code=429,
                detail=RATE_LIMIT_DETAIL,
            )

        ip_timestamps.append(current_time)
        self._global.append(current_time)


rate_limiter = RateLimiter()


def rate_limit_dependency(request: Request) -> None:
    client_ip = (
        request.client.host if request.client else "unknown"
    )
    rate_limiter.check_and_record(client_ip)
