from unittest.mock import patch

from app.facilities.orchestration import (
    ScoredRoute,
    _select_mix_portfolio,
    natural_match_pool,
    score_candidates,
)
from app.facilities.scoring import FacilityScore, is_fully_valid
from app.generation.routes import GeneratedRoute, QualityMetrics
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


def test_no_requirements_requests_exactly_count_candidates() -> None:
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
        mocked.assert_called_once_with(graph, Coordinate(lat=40.0, lon=-73.0), 8000.0, "mix", 3)


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


def _route(shape: str, edge_reuse_ratio: float, distance_m: float = 8000.0) -> GeneratedRoute:
    geometry = (
        RoutePoint(lat=40.0, lon=-73.0, elevation_m=0.0),
        RoutePoint(lat=40.01, lon=-73.0, elevation_m=0.0),
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


def _scored(
    shape: str,
    lat_offset: float,
    satisfied: int,
    total: int,
    distance_error_m: float = 0.0,
) -> ScoredRoute:
    """Build a `ScoredRoute` with a controlled hard-constraint tier
    (satisfied/total requirements, distance error) for `_select_mix_portfolio`
    tests, bypassing real facility geometry/scoring entirely. `lat_offset`
    keeps each fixture's geometry distinct so `select_diverse` never treats
    two fixtures as overlapping."""
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
    facility_score = FacilityScore(
        requirement_results=(),
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
