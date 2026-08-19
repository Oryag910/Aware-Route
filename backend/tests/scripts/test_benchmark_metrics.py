"""Focused, synthetic tests for the PR10 benchmark measurement helpers in
scripts/benchmark_suite.py: the short-start-return-spur detector and the
undirected route-segment overlap (diversity) metric. No graph load --
geometry is built by hand with app.routing.geometry.destination_point so
segment lengths are exact.
"""

from app.routing.geometry import destination_point
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint
from scripts.benchmark_scenarios import Scenario
from scripts.benchmark_suite import (
    ScenarioResult,
    SHORT_SPUR_MAX_PATH_M,
    SHORT_SPUR_MIN_PATH_M,
    _pairwise_segment_overlaps,
    _route_segment_signature,
    _segment_overlap,
    _short_start_return_spur,
)


ORIGIN = Coordinate(lat=40.7128, lon=-74.0060)


def _point(coord: Coordinate) -> RoutePoint:
    return RoutePoint(lat=coord.lat, lon=coord.lon, elevation_m=0.0)


def _walk(start: Coordinate, legs: list[tuple[float, float]]) -> tuple[RoutePoint, ...]:
    """Builds a RoutePoint chain from `start`, walking each (bearing_deg,
    distance_m) leg in turn. Uses the exact same spherical model as
    haversine_m (both derive from EARTH_RADIUS_M), so segment lengths
    below are exact modulo floating-point rounding."""
    points = [start]
    current = start
    for bearing, distance in legs:
        current = destination_point(current, bearing, distance)
        points.append(current)
    return tuple(_point(c) for c in points)


def _candidate(geometry: tuple[RoutePoint, ...]) -> RouteCandidate:
    return RouteCandidate(geometry=geometry, distance_m=0.0, elevation_gain_m=0.0)


def _scenario(shape: str = "round") -> Scenario:
    return Scenario(
        name="synthetic",
        start_lat=ORIGIN.lat,
        start_lon=ORIGIN.lon,
        target_distance_miles=2.0,
        shape=shape,  # type: ignore[arg-type]
        elevation_preference="flat",
        amenity_required=False,
    )


# ---------------------------------------------------------------------------
# _short_start_return_spur
# ---------------------------------------------------------------------------


def test_short_spur_straight_path_is_false() -> None:
    geometry = _walk(ORIGIN, [(0.0, 50.0)] * 10)  # 500m due north, no revisit

    assert _short_start_return_spur(geometry) is False


def test_short_spur_ordinary_bend_with_dense_points_is_false() -> None:
    # A dense right-angle bend (5m steps) starting at ORIGIN: 10 steps
    # north, then 10 steps east. This never returns near geometry[0] --
    # it's a one-way bend, not a there-and-back spur.
    leg1 = [(0.0, 5.0)] * 10
    leg2 = [(90.0, 5.0)] * 10
    geometry = _walk(ORIGIN, leg1 + leg2)

    assert _short_start_return_spur(geometry) is False


def test_short_spur_genuine_spur_at_start_is_true() -> None:
    # Mirrors app.generation.length_tune._spur_path: walk out from the
    # route's start, then retrace the same path back to the start,
    # before the "real" route continues. This is exactly the artifact
    # this metric targets.
    geometry = _walk(ORIGIN, [(90.0, 30.0), (270.0, 30.0), (0.0, 500.0)])

    assert _short_start_return_spur(geometry) is True


def test_short_spur_normal_out_and_back_turnaround_is_false() -> None:
    # THE CRITICAL REGRESSION CASE: a normal out_and_back route
    # (app.generation.out_and_back.out_and_back_path) is
    # start -> ... -> turnaround -> reverse(outbound) -> start. It
    # legitimately returns to its start after traveling the FULL route
    # distance, and its only short closed sub-path is around the
    # turnaround in the middle -- neither should read as a
    # start-anchored spur, because this metric only scans from
    # geometry[0] forward and stops once cumulative travel exceeds
    # SHORT_SPUR_MAX_PATH_M, long before the route closes back on
    # itself hundreds/thousands of meters later.
    outbound_legs = [(0.0, 100.0)] * 6  # 600m out (bearing north)
    outbound = _walk(ORIGIN, outbound_legs)
    # retrace the same path back to ORIGIN (mirrors out_and_back_path's
    # `path + reversed(path)[1:]`)
    geometry = outbound + tuple(reversed(outbound))[1:]

    assert len(geometry) > 2
    # sanity: the route really does end back at (~)its own start
    assert geometry[0].lat == geometry[-1].lat
    assert geometry[0].lon == geometry[-1].lon

    assert _short_start_return_spur(geometry) is False


def test_short_spur_small_loop_in_middle_of_route_is_false() -> None:
    # A genuine small closed loop, but located in the MIDDLE of an
    # otherwise ordinary route rather than at the start. This metric
    # specifically measures the length-tuner's start-anchored prefix,
    # not generic route topology, so a mid-route loop should not count.
    lead_in = [(0.0, 300.0)]  # 300m north before the loop even starts
    small_loop = [(90.0, 20.0), (0.0, 20.0), (270.0, 20.0), (180.0, 20.0)]
    tail = [(0.0, 300.0)]
    geometry = _walk(ORIGIN, lead_in + small_loop + tail)

    assert _short_start_return_spur(geometry) is False


def test_short_spur_start_return_above_maximum_is_false() -> None:
    # Returns to the exact start, but only after 400m of travel --
    # beyond SHORT_SPUR_MAX_PATH_M (250m), so this is route structure
    # (e.g. a full loop), not a "short" tuning artifact.
    side_m = 100.0
    assert 4 * side_m > SHORT_SPUR_MAX_PATH_M

    geometry = _walk(
        ORIGIN,
        [(90.0, side_m), (0.0, side_m), (270.0, side_m), (180.0, side_m)],
    )

    assert _short_start_return_spur(geometry) is False


def test_short_spur_start_return_below_minimum_is_false() -> None:
    # Genuinely returns to ORIGIN -- the last point IS geometry[0], so
    # the start-return condition is unquestionably satisfied here. Only
    # the traveled distance (~6m: 3m out + 3m back) is below
    # SHORT_SPUR_MIN_PATH_M (40m), which is what this test proves: a
    # real but too-small out-and-back doesn't count as a spur.
    away = destination_point(ORIGIN, 90.0, 3.0)  # 3m east
    geometry = (_point(ORIGIN), _point(away), _point(ORIGIN))

    assert 2 * 3.0 < SHORT_SPUR_MIN_PATH_M
    assert _short_start_return_spur(geometry) is False


# ---------------------------------------------------------------------------
# _route_segment_signature / _segment_overlap
# ---------------------------------------------------------------------------

_A = RoutePoint(lat=0.0, lon=0.0, elevation_m=0.0)
_B = RoutePoint(lat=0.0, lon=0.001, elevation_m=0.0)
_C = RoutePoint(lat=0.001, lon=0.001, elevation_m=0.0)
_D = RoutePoint(lat=0.001, lon=0.0, elevation_m=0.0)
_FAR1 = RoutePoint(lat=5.0, lon=5.0, elevation_m=0.0)
_FAR2 = RoutePoint(lat=5.0, lon=5.001, elevation_m=0.0)
_FAR3 = RoutePoint(lat=5.001, lon=5.001, elevation_m=0.0)


def test_segment_overlap_identical_route_is_one() -> None:
    segments = _route_segment_signature((_A, _B, _C))

    assert _segment_overlap(segments, segments) == 1.0


def test_segment_overlap_same_route_reversed_is_one() -> None:
    forward = _route_segment_signature((_A, _B, _C))
    reversed_ = _route_segment_signature((_C, _B, _A))

    assert forward == reversed_
    assert _segment_overlap(forward, reversed_) == 1.0


def test_segment_overlap_disjoint_routes_is_zero() -> None:
    route_a = _route_segment_signature((_A, _B, _C))
    route_b = _route_segment_signature((_FAR1, _FAR2, _FAR3))

    assert _segment_overlap(route_a, route_b) == 0.0


def test_segment_overlap_partial_share_is_correct_fraction() -> None:
    # route1: A-B, B-C. route2: A-B, B-D. Shared: {A-B}. Union: {A-B,
    # B-C, B-D} -- overlap = 1/3.
    route1 = _route_segment_signature((_A, _B, _C))
    route2 = _route_segment_signature((_A, _B, _D))

    assert _segment_overlap(route1, route2) == 1 / 3


def test_segment_signature_ignores_zero_length_duplicate_points() -> None:
    without_dup = _route_segment_signature((_A, _B, _C))
    with_dup = _route_segment_signature((_A, _A, _B, _B, _C))

    assert with_dup == without_dup


# ---------------------------------------------------------------------------
# scenario-level distinct-alternative classification
# ---------------------------------------------------------------------------


def test_pairwise_segment_overlaps_empty_for_fewer_than_two_candidates() -> None:
    assert _pairwise_segment_overlaps([]) == ()
    assert _pairwise_segment_overlaps([_candidate((_A, _B, _C))]) == ()


def test_pairwise_segment_overlaps_computes_all_pairs() -> None:
    candidates = [
        _candidate((_A, _B, _C)),
        _candidate((_A, _B, _D)),
        _candidate((_FAR1, _FAR2, _FAR3)),
    ]

    overlaps = _pairwise_segment_overlaps(candidates)

    # 3 candidates -> 3 pairs: (0,1)=1/3, (0,2)=0.0, (1,2)=0.0
    assert len(overlaps) == 3
    assert sorted(overlaps) == sorted([1 / 3, 0.0, 0.0])


def test_scenario_result_has_distinct_alternative_true_below_threshold() -> None:
    result = ScenarioResult(
        scenario=_scenario(),
        ok=True,
        error=None,
        time_s=0.1,
        segment_overlaps=(0.9, 0.5),
    )

    assert result.has_distinct_alternative is True


def test_scenario_result_has_distinct_alternative_false_when_all_overlap_high() -> None:
    result = ScenarioResult(
        scenario=_scenario(),
        ok=True,
        error=None,
        time_s=0.1,
        segment_overlaps=(0.95, 0.99),
    )

    assert result.has_distinct_alternative is False


def test_scenario_result_has_distinct_alternative_false_when_no_overlaps() -> None:
    result = ScenarioResult(
        scenario=_scenario(),
        ok=True,
        error=None,
        time_s=0.1,
        segment_overlaps=(),
    )

    assert result.has_distinct_alternative is False
