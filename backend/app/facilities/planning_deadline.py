"""Cooperative wall-clock budget for a single `/routes` planning call.

Constrained facility planners run synchronous, CPU-heavy NetworkX work
in-process. Wrapping that work in `asyncio.wait_for` would not actually
interrupt it -- cancellation only takes effect at an `await` point, and
none of the graph-build/Dijkstra work yields control. Instead, planners
check `PlanningDeadline.expired()` cooperatively between expensive steps
(before each real graph build) and simply stop proposing new candidates
once the budget is spent.

The deadline only limits SEARCH EFFORT. It never decides correctness --
a candidate built before the deadline expired is scored exactly like any
other by the normal scorer/ranker, and an expired deadline just means
"stop looking for more," not "discard what you already found."
"""

from __future__ import annotations

import os
import time

# Centralized default so the budget isn't a magic number scattered across
# modules. Chosen so a difficult multi-facility request stays well clear
# of Vercel's 300s proxy timeout and the frontend's own fail-safe timeout
# (see `ROUTE_REQUEST_TIMEOUT_MS` in the frontend), while still leaving
# constrained planners enough time to find genuinely useful candidates.
DEFAULT_BUDGET_S = 25.0

_ENV_VAR = "ROUTE_PLANNING_BUDGET_S"


def _resolve_budget_s() -> float:
    raw = os.environ.get(_ENV_VAR)
    if not raw:
        return DEFAULT_BUDGET_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_S
    if value <= 0:
        # A non-positive budget would make every planner call a no-op
        # (or, if negative logic were mishandled, an unbounded one) --
        # neither is an intentional configuration, so fall back safely
        # rather than propagate a malformed value.
        return DEFAULT_BUDGET_S
    return value


class PlanningDeadline:
    """Monotonic-clock deadline for one planning call.

    Uses `time.perf_counter()` rather than wall-clock datetime, which is
    not guaranteed monotonic and can jump across a request (NTP sync,
    system sleep/resume).
    """

    def __init__(self, budget_s: float | None = None) -> None:
        self.budget_s = _resolve_budget_s() if budget_s is None else budget_s
        self._started_at = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._started_at

    def remaining(self) -> float:
        return max(0.0, self.budget_s - self.elapsed())

    def expired(self) -> bool:
        return self.elapsed() >= self.budget_s
