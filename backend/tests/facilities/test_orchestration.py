import time
from typing import Any
from unittest.mock import patch

import pytest

from app.facilities.models import Facility
from app.facilities.orchestration import (
    ConstrainedPlanner,
    ScoredRoute,
    _select_mix_portfolio,
    natural_match_pool,
    plan_routes,
    score_candidates,
)
from app.facilities.models import FacilityRequirement, RequirementResult
from app.facilities.planning_deadline import PlanningDeadline
from app.facilities.scoring import FacilityScore, is_fully_valid
from app.generation.routes import GeneratedRoute, QualityMetrics
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


def test_no_requirements_requests_bounded_overcomplete_pool() -> None:
    """`requirements=[]` still skips every facility-matching cost, but no
    longer asks for exactly `count` raw candidates -- an overcomplete
    pool (see `NO_FACILITY_POOL_MULTIPLIER`/`_CEILING`) gives
    `select_diverse` real alternatives instead of hoping all `count` raw
    candidates survive construction and diversity filtering."""
    graph = object()
    with patch("app.facilities.orchestration.generate_routes") as mocked:
        mocked.return_value = []
        natural_match_pool(
            graph=graph,
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="mix",
            count=3,
            requirements=[],
        )
        args, kwargs = mocked.call_args
        called_pool_size = args[4]
        assert called_pool_size > 3
        assert kwargs.get("result_count") == called_pool_size


def test_no_requirements_pool_size_bounded_independent_of_count() -> None:
    """The overcomplete pool is bounded (`NO_FACILITY_POOL_CEILING`), not
    a fixed multiple of `count` -- latency shouldn't grow unbounded for
    the API's max count."""
    with patch("app.facilities.orchestration.generate_routes") as mocked:
        mocked.return_value = []
        natural_match_pool(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="mix",
            count=5,
            requirements=[],
        )
        called_pool_size = mocked.call_args[0][4]
        assert 5 < called_pool_size <= 10


def test_with_requirements_requests_overcomplete_pool() -> None:
    from app.facilities.models import FacilityRequirement

    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    with patch("app.facilities.orchestration.generate_routes") as mocked:
        mocked.return_value = []
        natural_match_pool(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="mix",
            count=3,
            requirements=[req],
        )
        called_pool_size = mocked.call_args[0][4]
        assert called_pool_size > 3


def _route(
    shape: str, edge_reuse_ratio: float, distance_m: float = 8000.0, lat_offset: float = 0.0
) -> GeneratedRoute:
    geometry = (
        RoutePoint(lat=40.0 + lat_offset, lon=-73.0, elevation_m=0.0),
        RoutePoint(lat=40.01 + lat_offset, lon=-73.0, elevation_m=0.0),
    )
    candidate = RouteCandidate(geometry=geometry, distance_m=distance_m, elevation_gain_m=0.0)
    quality = QualityMetrics(
        edge_reuse_ratio=edge_reuse_ratio,
        pedestrian_share=0.5,
        elevation_gain_m=0.0,
        corrective_loop_penalty=0.0,
        isoperimetric_quotient=0.5,
        waytype_breakdown={},
    )
    return GeneratedRoute(candidate=candidate, node_path=[1, 2], shape=shape, quality=quality)  # type: ignore[arg-type]


def test_out_and_back_edge_reuse_not_penalized_in_quality_score() -> None:
    """A concrete out-and-back's ~0.5 edge_reuse_ratio (retracing the
    outbound leg on the return, its defining feature) must not be
    scored worse than a round route with the SAME edge_reuse_ratio --
    that ratio is only a defect for a round/mix candidate."""
    oab_route = _route("out_and_back", edge_reuse_ratio=0.5)
    round_route = _route("round", edge_reuse_ratio=0.5)

    scored = score_candidates([oab_route, round_route], 8000.0, [], [])
    by_shape = {s.route.shape: s.quality_score for s in scored}

    assert by_shape["out_and_back"] < by_shape["round"]
    # The OAB's own 0.5 edge reuse is fully excused -- quality_score
    # reflects only the (identical, in this fixture) pedestrian_share term.
    assert by_shape["out_and_back"] == 0.5  # 1.0 - pedestrian_share(0.5)


def _requirement_result(satisfied: bool, range_error_m: float, index: int) -> RequirementResult:
    requirement = FacilityRequirement(
        id=f"req-{index}", kind="restroom", min_distance_m=0.0, max_distance_m=1000.0
    )
    return RequirementResult(
        requirement=requirement, satisfied=satisfied, range_error_m=range_error_m, encounter=None
    )


def _scored(
    shape: str,
    lat_offset: float,
    satisfied: int,
    total: int,
    distance_error_m: float = 0.0,
    worst_range_error_m: float = 0.0,
) -> ScoredRoute:
    """Build a `ScoredRoute` with a controlled hard-constraint tier
    (satisfied/total requirements, distance error, and -- via real
    `RequirementResult` fixtures -- facility range-error miss magnitude)
    for `_select_mix_portfolio` tests, bypassing real facility
    geometry/scoring entirely. Every unsatisfied requirement gets the
    same `worst_range_error_m` miss (sufficient to control both the
    worst-single-requirement and total range error `rank_key` tiers in
    these tests); satisfied ones are exact (0.0), matching how
    `assign_requirements` scores a hit. `lat_offset` keeps each fixture's
    geometry distinct so `select_diverse` never treats two fixtures as
    overlapping."""
    geometry = (
        RoutePoint(lat=40.0 + lat_offset, lon=-73.0, elevation_m=0.0),
        RoutePoint(lat=40.01 + lat_offset, lon=-73.0, elevation_m=0.0),
    )
    candidate = RouteCandidate(geometry=geometry, distance_m=8000.0, elevation_gain_m=0.0)
    quality = QualityMetrics(
        edge_reuse_ratio=0.0,
        pedestrian_share=1.0,
        elevation_gain_m=0.0,
        corrective_loop_penalty=0.0,
        isoperimetric_quotient=0.5,
        waytype_breakdown={},
    )
    route = GeneratedRoute(candidate=candidate, node_path=[1, 2], shape=shape, quality=quality)  # type: ignore[arg-type]
    requirement_results = tuple(
        _requirement_result(True, 0.0, index) for index in range(satisfied)
    ) + tuple(
        _requirement_result(False, worst_range_error_m, satisfied + index)
        for index in range(total - satisfied)
    )
    facility_score = FacilityScore(
        requirement_results=requirement_results,
        requirements_total=total,
        requirements_satisfied_count=satisfied,
        all_satisfied=satisfied == total,
    )
    return ScoredRoute(
        route=route,
        distance_error_m=distance_error_m,
        facility_score=facility_score,
        quality_score=0.0,
        fully_valid=is_fully_valid(geometry, distance_error_m, facility_score),
    )


def test_mix_portfolio_never_lets_partial_shape_displace_fully_valid() -> None:
    """count=2, two fully-valid rounds and one partial OAB -- the
    round/OAB (1, 1) shape quota must NOT pull in the partial OAB over
    the second fully-valid round."""
    round_a = _scored("round", 0.00, satisfied=1, total=1)
    round_b = _scored("round", 0.01, satisfied=1, total=1)
    oab_c = _scored("out_and_back", 0.02, satisfied=0, total=1)
    # Already best-to-worst: both fully-valid rounds outrank the partial OAB.
    scored = [round_a, round_b, oab_c]

    result = _select_mix_portfolio(scored, 2)

    assert len(result) == 2
    assert all(item.fully_valid and item.route.shape == "round" for item in result)


def test_mix_portfolio_picks_one_of_each_shape_when_both_fully_valid() -> None:
    """count=2, one fully-valid round and one fully-valid OAB -- both are
    in the top tier and both fit, so the (1, 1) shape allocation holds."""
    round_a = _scored("round", 0.00, satisfied=1, total=1)
    oab_a = _scored("out_and_back", 0.01, satisfied=1, total=1)

    result = _select_mix_portfolio([round_a, oab_a], 2)

    assert {item.route.shape for item in result} == {"round", "out_and_back"}


def test_mix_portfolio_better_partial_not_displaced_by_worse_shape_quota_fill() -> None:
    """Among partial candidates, a route satisfying more requirements
    must not be dropped in favor of a much worse route from the other
    shape merely to fill the round/OAB quota."""
    round_a = _scored("round", 0.00, satisfied=3, total=4, distance_error_m=0.0)
    round_b = _scored("round", 0.01, satisfied=3, total=4, distance_error_m=5.0)
    oab_c = _scored("out_and_back", 0.02, satisfied=0, total=4, distance_error_m=0.0)
    scored = [round_a, round_b, oab_c]  # already best-to-worst: 3/4, 3/4, 0/4

    result = _select_mix_portfolio(scored, 2)

    assert result == [round_a, round_b]
    assert oab_c not in result


def test_mix_portfolio_prefers_shape_diversity_within_equivalent_tier() -> None:
    """When candidates are equivalent on hard-constraint quality (all
    fully valid here), shape diversity still governs which two are kept."""
    round_a = _scored("round", 0.00, satisfied=2, total=2, distance_error_m=0.0)
    round_b = _scored("round", 0.01, satisfied=2, total=2, distance_error_m=5.0)
    oab_a = _scored("out_and_back", 0.02, satisfied=2, total=2, distance_error_m=1.0)
    oab_b = _scored("out_and_back", 0.03, satisfied=2, total=2, distance_error_m=6.0)
    # Already best-to-worst within the shared fully-valid tier.
    scored = [round_a, oab_a, round_b, oab_b]

    result = _select_mix_portfolio(scored, 2)

    assert {item.route.shape for item in result} == {"round", "out_and_back"}
    assert round_a in result
    assert oab_a in result


def test_mix_portfolio_worse_facility_miss_not_masked_by_tied_satisfied_count() -> None:
    """Two partial routes can tie on requirements-satisfied count while
    differing sharply in HOW BADLY the failing requirement misses its
    window -- that miss magnitude (worst/total range error) is part of
    the hard-constraint tier too, so a materially worse miss must not
    sneak in over a better one just to fill the round/OAB shape quota.
    Round A (10m worst miss) and Round B (20m worst miss) are each their
    own tier, both strictly better than OAB C's 1000m miss -- count=2
    exhausts on the two rounds before OAB C's tier is ever considered."""
    round_a = _scored(
        "round", 0.00, satisfied=3, total=4, worst_range_error_m=10.0
    )
    round_b = _scored(
        "round", 0.01, satisfied=3, total=4, worst_range_error_m=20.0
    )
    oab_c = _scored(
        "out_and_back", 0.02, satisfied=3, total=4, worst_range_error_m=1000.0
    )
    scored = [round_a, round_b, oab_c]  # already best-to-worst per rank_key

    result = _select_mix_portfolio(scored, 2)

    assert result == [round_a, round_b]
    assert oab_c not in result


# --- Progressive constrained planning -----------------------------------


def _make_planner(name: str, batches: list[list[GeneratedRoute]]) -> tuple[ConstrainedPlanner, list[int]]:
    """A fake constrained planner returning one pre-built batch of
    `GeneratedRoute`s per call (empty once `batches` is exhausted), so
    tests can assert exactly how many times -- if any -- it was
    invoked."""
    call_count = [0]

    def planner(
        graph: Any,
        start: Coordinate,
        target_distance_m: float,
        shape: str,
        count: int,
        requirements: list[FacilityRequirement],
        facilities: list[Facility],
        deadline: PlanningDeadline,
    ) -> list[GeneratedRoute]:
        index = call_count[0]
        call_count[0] += 1
        return batches[index] if index < len(batches) else []

    planner.__name__ = name
    return planner, call_count


_REQ = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)


def test_progressive_planning_skips_second_planner_when_first_is_sufficient() -> None:
    """Planner 1 alone makes the pool fully sufficient for `count` -- the
    second planner must never be called."""
    planner_one, calls_one = _make_planner(
        "planner_one",
        [
            [
                _route("round", 0.0, 8000.0, lat_offset=3.00),
                _route("round", 0.0, 8000.0, lat_offset=3.01),
                _route("round", 0.0, 8000.0, lat_offset=3.02),
            ]
        ],
    )
    planner_two, calls_two = _make_planner(
        "planner_two", [[_route("round", 0.0, 8000.0, lat_offset=4.00)]]
    )

    natural_scored = [_scored("round", 0.00, satisfied=0, total=1)]
    sufficient_scored = [
        _scored("round", 0.01, satisfied=1, total=1),
        _scored("round", 0.02, satisfied=1, total=1),
        _scored("round", 0.03, satisfied=1, total=1),
    ]

    with (
        patch(
            "app.facilities.orchestration.natural_match_pool",
            return_value=[_route("round", 0.0, 8000.0)],
        ),
        patch(
            "app.facilities.orchestration.score_candidates",
            side_effect=[natural_scored, sufficient_scored],
        ),
    ):
        result = plan_routes(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="round",
            count=3,
            requirements=[_REQ],
            facilities=[],
            constrained_planners=[planner_one, planner_two],
        )

    assert calls_one[0] == 1
    assert calls_two[0] == 0
    assert len(result) == 3


def test_progressive_planning_runs_second_planner_when_still_insufficient() -> None:
    """Planner 1 alone isn't enough -- planner 2 must still run."""
    planner_one, calls_one = _make_planner(
        "planner_one", [[_route("round", 0.0, 8000.0, lat_offset=3.00)]]
    )
    planner_two, calls_two = _make_planner(
        "planner_two",
        [
            [
                _route("round", 0.0, 8000.0, lat_offset=4.00),
                _route("round", 0.0, 8000.0, lat_offset=4.01),
            ]
        ],
    )

    natural_scored = [_scored("round", 0.00, satisfied=0, total=1)]
    after_planner_one = [_scored("round", 0.01, satisfied=0, total=1)]
    after_planner_two = [
        _scored("round", 0.02, satisfied=1, total=1),
        _scored("round", 0.03, satisfied=1, total=1),
        _scored("round", 0.04, satisfied=1, total=1),
    ]

    with (
        patch(
            "app.facilities.orchestration.natural_match_pool",
            return_value=[_route("round", 0.0, 8000.0)],
        ),
        patch(
            "app.facilities.orchestration.score_candidates",
            side_effect=[natural_scored, after_planner_one, after_planner_two],
        ),
    ):
        result = plan_routes(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="round",
            count=3,
            requirements=[_REQ],
            facilities=[],
            constrained_planners=[planner_one, planner_two],
        )

    assert calls_one[0] == 1
    assert calls_two[0] == 1
    assert len(result) == 3


def test_no_facility_request_never_invokes_constrained_planners() -> None:
    """Regression guard: an ordinary no-facility request must never pay
    for constrained-planner search machinery, progressive or not."""
    planner_one, calls_one = _make_planner("planner_one", [[_route("round", 0.0, 8000.0)]])
    planner_two, calls_two = _make_planner("planner_two", [[_route("round", 0.0, 8000.0)]])

    with patch(
        "app.facilities.orchestration.natural_match_pool",
        return_value=[_route("round", 0.0, 8000.0)],
    ):
        plan_routes(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="round",
            count=3,
            requirements=[],
            facilities=[],
            constrained_planners=[planner_one, planner_two],
        )

    assert calls_one[0] == 0
    assert calls_two[0] == 0


def test_deadline_starts_before_natural_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The planning deadline must cover natural generation + scoring, not
    just the constrained-planner phase -- a slow natural phase should
    leave correspondingly less of the total budget for planners, rather
    than each phase getting its own fresh budget."""
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "0.2")

    def slow_natural_match_pool(*_args: object, **_kwargs: object) -> list[GeneratedRoute]:
        time.sleep(0.15)
        return [_route("round", 0.0, 8000.0)]

    captured: dict[str, float] = {}

    def planner(
        graph: Any, start: Coordinate, target_distance_m: float, shape: str, count: int,
        requirements: list[FacilityRequirement], facilities: list[Facility],
        deadline: PlanningDeadline,
    ) -> list[GeneratedRoute]:
        captured["remaining"] = deadline.remaining()
        return []

    planner.__name__ = "spy_planner"

    with patch(
        "app.facilities.orchestration.natural_match_pool",
        side_effect=slow_natural_match_pool,
    ):
        plan_routes(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="round",
            count=3,
            requirements=[_REQ],
            facilities=[],
            constrained_planners=[planner],
        )

    # Of the 0.2s total budget, ~0.15s was already spent by the time the
    # constrained planner runs -- it must see well under the full budget,
    # not a fresh 0.2s of its own.
    assert captured["remaining"] < 0.1
