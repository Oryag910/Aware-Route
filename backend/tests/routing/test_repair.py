import pytest

from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint
from app.routing.repair import (
    MAX_DISTANCE_ERROR_M,
    NEAR_MISS_RATIO,
    repair_near_miss_candidates,
)


START = Coordinate(lat=40.70, lon=-74.00)
TARGET_DISTANCE_M = 2220.0

# 0.15 * 2220.0 = 333.0 -- anything past this is "too far gone" per
# NEAR_MISS_RATIO, anything within MAX_DISTANCE_ERROR_M (100.0) is
# already accurate. The near-miss band is (100.0, 333.0].
NEAR_MISS_UPPER_BOUND_M = NEAR_MISS_RATIO * TARGET_DISTANCE_M


def make_candidate(distance_m: float) -> RouteCandidate:
    return RouteCandidate(
        geometry=(
            RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
            RoutePoint(lat=40.71, lon=-74.00, elevation_m=10.0),
            RoutePoint(lat=40.72, lon=-74.00, elevation_m=5.0),
        ),
        distance_m=distance_m,
        elevation_gain_m=10.0,
    )


class FakeRepairProvider:
    def __init__(
        self,
        waypoint_responses: list[RouteCandidate | Exception],
    ) -> None:
        self._responses = list(waypoint_responses)
        self.calls: list[list[Coordinate]] = []

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        raise NotImplementedError(
            "repair should only call get_route_through_waypoints"
        )

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        self.calls.append(waypoints)
        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def test_already_accurate_candidates_are_left_untouched() -> None:
    candidate = make_candidate(TARGET_DISTANCE_M + MAX_DISTANCE_ERROR_M)
    provider = FakeRepairProvider(waypoint_responses=[])

    result = repair_near_miss_candidates(
        provider, [candidate], START, TARGET_DISTANCE_M
    )

    assert result == [candidate]
    assert provider.calls == []


def test_too_far_gone_candidates_are_left_untouched() -> None:
    candidate = make_candidate(
        TARGET_DISTANCE_M + NEAR_MISS_UPPER_BOUND_M + 1.0
    )
    provider = FakeRepairProvider(waypoint_responses=[])

    result = repair_near_miss_candidates(
        provider, [candidate], START, TARGET_DISTANCE_M
    )

    assert result == [candidate]
    assert provider.calls == []


def test_near_miss_candidate_is_corrected_when_first_attempt_converges() -> None:  # noqa: E501
    near_miss = make_candidate(TARGET_DISTANCE_M + 200.0)
    corrected = make_candidate(TARGET_DISTANCE_M)
    provider = FakeRepairProvider(waypoint_responses=[corrected])

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [corrected]
    # Stops after the first attempt since it already hit the target.
    assert len(provider.calls) == 1


def test_repair_keeps_the_best_attempt_across_rounds() -> None:
    near_miss = make_candidate(TARGET_DISTANCE_M + 200.0)
    # None of the 3 attempts (MAX_REPAIR_ROUNDS) hits the ±100m
    # constraint, so repair runs all 3 rounds -- it should keep
    # whichever attempt had the smallest error overall (the second),
    # not just the last one that happened to run.
    worse_attempt = make_candidate(TARGET_DISTANCE_M + 150.0)
    better_attempt = make_candidate(TARGET_DISTANCE_M + 120.0)
    final_attempt = make_candidate(TARGET_DISTANCE_M + 140.0)

    provider = FakeRepairProvider(
        waypoint_responses=[worse_attempt, better_attempt, final_attempt]
    )

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [better_attempt]
    assert len(provider.calls) == 3


def test_route_not_found_mid_repair_falls_back_to_best_so_far() -> None:
    near_miss = make_candidate(TARGET_DISTANCE_M + 200.0)
    first_attempt = make_candidate(TARGET_DISTANCE_M + 120.0)

    provider = FakeRepairProvider(
        waypoint_responses=[
            first_attempt,
            RouteNotFoundError("no route for this waypoint set"),
        ]
    )

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    # Repair must not raise -- it should just stop and keep the best
    # candidate found before the failure.
    assert result == [first_attempt]


def test_shared_budget_is_spent_across_multiple_near_miss_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routing.repair.MAX_REPAIR_CALLS_PER_REQUEST", 1
    )

    first_near_miss = make_candidate(TARGET_DISTANCE_M + 200.0)
    second_near_miss = make_candidate(TARGET_DISTANCE_M + 250.0)

    first_attempt = make_candidate(TARGET_DISTANCE_M + 150.0)

    provider = FakeRepairProvider(waypoint_responses=[first_attempt])

    result = repair_near_miss_candidates(
        provider,
        [first_near_miss, second_near_miss],
        START,
        TARGET_DISTANCE_M,
    )

    # Only 1 call in the shared budget -- spent on the first candidate,
    # leaving the second untouched.
    assert result == [first_attempt, second_near_miss]
    assert len(provider.calls) == 1
