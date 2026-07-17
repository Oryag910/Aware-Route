import pytest

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint
from app.routing.repair import (
    MAX_DISTANCE_ERROR_M,
    NEAR_MISS_RATIO,
    RESCUE_RATIO,
    RepairTarget,
    repair_near_miss_candidates,
)


@pytest.fixture(autouse=True)
def _single_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    # FakeRepairProvider below pops canned responses off a list in
    # call order, which is only deterministic with one worker at a
    # time -- repair still runs candidates one at a time, matching
    # the pre-parallelism call ordering the tests assert on.
    monkeypatch.setattr(
        "app.routing.parallel.MAX_PARALLEL_ROUTING_CALLS", 1
    )


START = Coordinate(lat=40.70, lon=-74.00)
TARGET_DISTANCE_M = 2220.0

# 0.15 * 2220.0 = 333.0 -- the first repair pass covers errors in
# (100.0, 333.0]. The rescue pass (only when nothing is within
# tolerance) extends to 0.5 * 2220.0 = 1110.0. Beyond that a candidate
# is never repaired.
NEAR_MISS_UPPER_BOUND_M = NEAR_MISS_RATIO * TARGET_DISTANCE_M
RESCUE_UPPER_BOUND_M = RESCUE_RATIO * TARGET_DISTANCE_M


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


def make_target(distance_m: float) -> RepairTarget:
    return RepairTarget(candidate=make_candidate(distance_m))


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
    target = make_target(TARGET_DISTANCE_M + MAX_DISTANCE_ERROR_M)
    provider = FakeRepairProvider(waypoint_responses=[])

    result = repair_near_miss_candidates(
        provider, [target], START, TARGET_DISTANCE_M
    )

    assert result == [target.candidate]
    assert provider.calls == []


def test_beyond_rescue_ratio_candidates_are_never_repaired() -> None:
    target = make_target(
        TARGET_DISTANCE_M + RESCUE_UPPER_BOUND_M + 1.0
    )
    provider = FakeRepairProvider(waypoint_responses=[])

    result = repair_near_miss_candidates(
        provider, [target], START, TARGET_DISTANCE_M
    )

    assert result == [target.candidate]
    assert provider.calls == []


def test_near_miss_candidate_is_corrected_when_first_attempt_converges() -> None:  # noqa: E501
    near_miss = make_target(TARGET_DISTANCE_M + 200.0)
    corrected = make_candidate(TARGET_DISTANCE_M)
    provider = FakeRepairProvider(waypoint_responses=[corrected])

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [corrected]
    # Stops after the first attempt since it already hit the target.
    assert len(provider.calls) == 1


def test_repair_keeps_the_best_attempt_across_rounds() -> None:
    near_miss = make_target(TARGET_DISTANCE_M + 200.0)
    # None of the 3 attempts (the per-candidate call cap) hits the
    # ±100m constraint -- repair must keep whichever attempt had the
    # smallest error overall (the second), not the last one that
    # happened to run.
    worse_attempt = make_candidate(TARGET_DISTANCE_M + 150.0)
    better_attempt = make_candidate(TARGET_DISTANCE_M + 120.0)
    final_attempt = make_candidate(TARGET_DISTANCE_M + 140.0)

    provider = FakeRepairProvider(
        waypoint_responses=[
            worse_attempt,
            better_attempt,
            final_attempt,
        ]
    )

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [better_attempt]
    assert len(provider.calls) == 3


def test_repair_tolerates_worse_intermediate_attempts_while_converging() -> None:  # noqa: E501
    # Reshaping a loop into an out-and-back always passes through a
    # much-worse intermediate attempt before the radial distance is
    # resized correctly. Repair must judge progress against the
    # previous attempt on the trajectory (3000 -> 600 -> 50, always
    # improving) rather than against the original candidate's 200.0
    # error -- the latter aborted convergence one round early in the
    # 2026-07-17 benchmark regression.
    near_miss = make_target(TARGET_DISTANCE_M + 200.0)
    much_worse = make_candidate(TARGET_DISTANCE_M + 3000.0)
    closer = make_candidate(TARGET_DISTANCE_M + 600.0)
    converged = make_candidate(TARGET_DISTANCE_M + 50.0)

    provider = FakeRepairProvider(
        waypoint_responses=[much_worse, closer, converged]
    )

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [converged]
    assert len(provider.calls) == 3


def test_unroutable_anchor_falls_back_to_next_anchor() -> None:
    near_miss = make_target(TARGET_DISTANCE_M + 200.0)
    corrected = make_candidate(TARGET_DISTANCE_M)

    # The first (furthest-point) anchor is unroutable -- e.g. nudged
    # into water. Repair should move to the fallback anchor rather
    # than abandoning the candidate.
    provider = FakeRepairProvider(
        waypoint_responses=[
            RouteNotFoundError("no route for this waypoint set"),
            corrected,
        ]
    )

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [corrected]
    assert len(provider.calls) == 2


def test_provider_error_aborts_only_that_candidate() -> None:
    # closer_near_miss (error 200) is more promising than
    # further_near_miss (error 250), so it's tried first within the
    # pass -- but both candidates are already "in flight" concurrently
    # by the time it fails, so the provider error must only abort
    # closer_near_miss's own repair, not further_near_miss's.
    closer_near_miss = make_target(TARGET_DISTANCE_M + 200.0)
    further_near_miss = make_target(TARGET_DISTANCE_M + 250.0)
    corrected = make_candidate(TARGET_DISTANCE_M)

    provider = FakeRepairProvider(
        waypoint_responses=[
            RoutingProviderError("rate limited"),
            corrected,
        ]
    )

    result = repair_near_miss_candidates(
        provider,
        [closer_near_miss, further_near_miss],
        START,
        TARGET_DISTANCE_M,
    )

    # closer_near_miss keeps its best-so-far (its original candidate,
    # since the only attempt failed); further_near_miss converges.
    assert result == [closer_near_miss.candidate, corrected]
    assert len(provider.calls) == 2


def test_provider_failure_in_pass_one_skips_rescue_pass() -> None:
    # Both candidates are near-misses so both are eligible for pass
    # one. The first one attempted hits a provider error; even though
    # neither candidate ends up within tolerance and budget remains,
    # the rescue pass must be skipped once any candidate in pass one
    # hit a provider failure.
    only_near_miss = make_target(TARGET_DISTANCE_M + 200.0)

    provider = FakeRepairProvider(
        waypoint_responses=[RoutingProviderError("rate limited")]
    )

    result = repair_near_miss_candidates(
        provider,
        [only_near_miss],
        START,
        TARGET_DISTANCE_M,
    )

    assert result == [only_near_miss.candidate]
    assert len(provider.calls) == 1


def test_shared_budget_is_spent_on_most_promising_candidate_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routing.repair.MAX_REPAIR_CALLS_PER_REQUEST", 1
    )

    # Listed worse-first: repair must still spend its single call on
    # the *closer* candidate (smallest error), not the first in list
    # order.
    further_near_miss = make_target(TARGET_DISTANCE_M + 250.0)
    closer_near_miss = make_target(TARGET_DISTANCE_M + 200.0)

    attempt = make_candidate(TARGET_DISTANCE_M + 150.0)

    provider = FakeRepairProvider(waypoint_responses=[attempt])

    result = repair_near_miss_candidates(
        provider,
        [further_near_miss, closer_near_miss],
        START,
        TARGET_DISTANCE_M,
    )

    # The single call went to closer_near_miss (improved to +150.0);
    # further_near_miss is untouched.
    assert result == [further_near_miss.candidate, attempt]
    assert len(provider.calls) == 1


def test_rescue_pass_repairs_moderate_misses_when_nothing_matches() -> None:
    # Error 400.0 is past the near-miss band (333.0) but inside the
    # rescue band (1110.0). With no candidate within tolerance, the
    # rescue pass must still try to fix it -- this is the Battery
    # Park scenario where every candidate misses badly.
    moderate_miss = make_target(TARGET_DISTANCE_M + 400.0)
    corrected = make_candidate(TARGET_DISTANCE_M)
    provider = FakeRepairProvider(waypoint_responses=[corrected])

    result = repair_near_miss_candidates(
        provider, [moderate_miss], START, TARGET_DISTANCE_M
    )

    assert result == [corrected]
    assert len(provider.calls) == 1


def test_rescue_pass_is_skipped_when_a_candidate_already_matches() -> None:
    accurate = make_target(TARGET_DISTANCE_M + 50.0)
    moderate_miss = make_target(TARGET_DISTANCE_M + 400.0)
    provider = FakeRepairProvider(waypoint_responses=[])

    result = repair_near_miss_candidates(
        provider, [accurate, moderate_miss], START, TARGET_DISTANCE_M
    )

    # A candidate already satisfies the distance constraint, so no
    # repair calls are spent widening eligibility.
    assert result == [accurate.candidate, moderate_miss.candidate]
    assert provider.calls == []


def test_repair_preserves_via_waypoint_for_restroom_first_candidates() -> None:  # noqa: E501
    restroom_waypoint = Coordinate(lat=40.715, lon=-74.005)
    near_miss = RepairTarget(
        candidate=make_candidate(TARGET_DISTANCE_M + 200.0),
        via=restroom_waypoint,
    )
    corrected = make_candidate(TARGET_DISTANCE_M)
    provider = FakeRepairProvider(waypoint_responses=[corrected])

    result = repair_near_miss_candidates(
        provider, [near_miss], START, TARGET_DISTANCE_M
    )

    assert result == [corrected]
    # The repair call must route start -> restroom -> anchor -> start,
    # keeping the restroom the candidate was built around.
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == START
    assert provider.calls[0][1] == restroom_waypoint
    assert provider.calls[0][-1] == START
    assert len(provider.calls[0]) == 4
