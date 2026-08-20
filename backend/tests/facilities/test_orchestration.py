from unittest.mock import patch

from app.facilities.orchestration import score_candidates, natural_match_pool
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
