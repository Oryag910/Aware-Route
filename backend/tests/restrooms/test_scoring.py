import pytest

from app.flow.elevation import detect_climbs
from app.flow.interruptions import InterruptionStore, _build_cell_index
from app.flow.shape import compactness, sharp_turn_count, u_turn_count
from app.restrooms.geo import RESTROOM_PROXIMITY_THRESHOLD_M, RestroomMatch
from app.restrooms.models import Restroom
from app.restrooms.scoring import (
    WEIGHT_OFF_ROUTE,
    WEIGHT_PROFILES,
    best_match_for_range,
    best_restroom_waypoint,
    elevation_bucket,
    elevation_mismatch_norm,
    mile_range_error_m,
    normalize_min_max,
    off_route_norm,
    restroom_confidence,
    score_and_rank_candidates,
)
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


EMPTY_INTERRUPTION_STORE = InterruptionStore(
    signals=(), crossings=(), signal_cell_index={}, crossing_cell_index={}
)


def make_restroom(
    source_id: str,
    latitude: float,
    longitude: float,
    *,
    hours_of_operation: str | None = None,
    accessibility: str | None = None,
) -> Restroom:
    return Restroom(
        source_id=source_id,
        facility_name=f"Restroom {source_id}",
        status="Operational",
        hours_of_operation=hours_of_operation,
        accessibility=accessibility,
        website=None,
        latitude=latitude,
        longitude=longitude,
    )


def make_match(
    source_id: str,
    mile_marker_m: float,
) -> RestroomMatch:
    restroom = make_restroom(
        source_id=source_id,
        latitude=40.70,
        longitude=-74.00,
    )

    return RestroomMatch(
        restroom=restroom,
        distance_to_route_m=10.0,
        mile_marker_m=mile_marker_m,
    )


def make_candidate(
    start_lat: float,
    longitude: float,
    distance_m: float,
) -> RouteCandidate:
    geometry = (
        RoutePoint(
            lat=start_lat,
            lon=longitude,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=start_lat + 0.01,
            lon=longitude,
            elevation_m=10.0,
        ),
        RoutePoint(
            lat=start_lat + 0.02,
            lon=longitude,
            elevation_m=5.0,
        ),
    )

    return RouteCandidate(
        geometry=geometry,
        distance_m=distance_m,
        elevation_gain_m=10.0,
    )


def make_candidate_with_gain(
    elevation_gain_m: float,
    distance_m: float = 1000.0,
) -> RouteCandidate:
    geometry = (
        RoutePoint(
            lat=40.70,
            lon=-74.00,
            elevation_m=0.0,
        ),
    )

    return RouteCandidate(
        geometry=geometry,
        distance_m=distance_m,
        elevation_gain_m=elevation_gain_m,
    )


LAT_STEP_DEG = 0.0002  # ~22m per point at this latitude
STEP_M = LAT_STEP_DEG * 111_320.0


def make_candidate_with_geometry(
    elevations: list[float],
    lat_step: float = LAT_STEP_DEG,
    longitude: float = -74.00,
    # Only used when the geometry-based distance doesn't matter for the
    # test in question -- pass None (the default) to derive distance_m
    # from the geometry's own point count instead.
    distance_m: float | None = None,
) -> RouteCandidate:
    geometry = tuple(
        RoutePoint(
            lat=40.70 + lat_step * index,
            lon=longitude,
            elevation_m=elevation,
        )
        for index, elevation in enumerate(elevations)
    )

    resolved_distance_m = (
        distance_m
        if distance_m is not None
        else lat_step * 111_320.0 * (len(elevations) - 1)
    )

    return RouteCandidate(
        geometry=geometry,
        distance_m=resolved_distance_m,
        # Deliberately wrong/unused raw total -- these tests exist to
        # prove the geometry-based path ignores this field once there
        # are >=2 points.
        elevation_gain_m=999.0,
    )


def test_mile_range_error_m_below_range() -> None:
    result = mile_range_error_m(
        mile_marker_m=500.0,
        min_mile_m=1000.0,
        max_mile_m=2000.0,
    )

    assert result == 500.0


def test_mile_range_error_m_inside_range() -> None:
    result = mile_range_error_m(
        mile_marker_m=1500.0,
        min_mile_m=1000.0,
        max_mile_m=2000.0,
    )

    assert result == 0.0


def test_mile_range_error_m_above_range() -> None:
    result = mile_range_error_m(
        mile_marker_m=2500.0,
        min_mile_m=1000.0,
        max_mile_m=2000.0,
    )

    assert result == 500.0


def test_best_match_for_range_picks_smallest_error() -> None:
    below_match = make_match(
        source_id="below",
        mile_marker_m=500.0,
    )
    closest_match = make_match(
        source_id="closest",
        mile_marker_m=1500.0,
    )
    above_match = make_match(
        source_id="above",
        mile_marker_m=2500.0,
    )

    result = best_match_for_range(
        [below_match, closest_match, above_match],
        min_mile_m=1000.0,
        max_mile_m=1400.0,
    )

    assert result is closest_match


def test_best_match_for_range_returns_none_for_empty_list() -> None:
    result = best_match_for_range(
        [],
        min_mile_m=1000.0,
        max_mile_m=2000.0,
    )

    assert result is None


def test_score_and_rank_candidates_drops_unmatched() -> None:
    candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=2200.0,
    )

    far_restroom = make_restroom(
        source_id="far",
        latitude=40.71,
        longitude=-73.99,
    )

    result = score_and_rank_candidates(
        candidates=[candidate],
        restrooms=[far_restroom],
        target_distance_m=2200.0,
        min_mile_m=500.0,
        max_mile_m=1500.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert result == []


def test_restroom_confidence_without_metadata() -> None:
    restroom = make_restroom(
        source_id="no-metadata",
        latitude=40.7000,
        longitude=-74.0000,
    )

    assert restroom_confidence(restroom) == pytest.approx(0.5)


def test_restroom_confidence_with_hours_only() -> None:
    restroom = make_restroom(
        source_id="hours-only",
        latitude=40.7000,
        longitude=-74.0000,
        hours_of_operation="8 AM–8 PM",
    )

    assert restroom_confidence(restroom) == pytest.approx(0.8)


def test_restroom_confidence_with_accessibility_only() -> None:
    restroom = make_restroom(
        source_id="accessibility-only",
        latitude=40.7000,
        longitude=-74.0000,
        accessibility="Accessible",
    )

    assert restroom_confidence(restroom) == pytest.approx(0.7)


def test_restroom_confidence_with_all_metadata() -> None:
    restroom = make_restroom(
        source_id="all-metadata",
        latitude=40.7000,
        longitude=-74.0000,
        hours_of_operation="8 AM–8 PM",
        accessibility="Accessible",
    )

    assert restroom_confidence(restroom) == pytest.approx(1.0)


def test_restroom_confidence_ignores_empty_strings() -> None:
    restroom = make_restroom(
        source_id="empty-strings",
        latitude=40.7000,
        longitude=-74.0000,
        hours_of_operation="",
        accessibility="",
    )

    assert restroom_confidence(restroom) == pytest.approx(0.5)


def test_elevation_bucket_boundaries() -> None:
    assert elevation_bucket(9.99) == "flat"
    assert elevation_bucket(10.0) == "moderate"
    assert elevation_bucket(25.0) == "moderate"
    assert elevation_bucket(25.01) == "hilly"


def test_elevation_mismatch_norm_matching_bucket_is_zero() -> None:
    candidate = make_candidate_with_gain(5.0)

    result = elevation_mismatch_norm(
        candidate,
        preferred_bucket="flat",
    )

    assert result == pytest.approx(0.0)


def test_elevation_mismatch_norm_adjacent_bucket_is_half() -> None:
    candidate = make_candidate_with_gain(15.0)

    result = elevation_mismatch_norm(
        candidate,
        preferred_bucket="flat",
    )

    assert result == pytest.approx(0.5)


def test_elevation_mismatch_norm_opposite_ends_is_one() -> None:
    candidate = make_candidate_with_gain(30.0)

    result = elevation_mismatch_norm(
        candidate,
        preferred_bucket="flat",
    )

    assert result == pytest.approx(1.0)


def test_elevation_mismatch_norm_uses_smoothed_geometry_gain() -> None:
    # 1000m route, flat except for a single-vertex DEM spike -- the raw
    # elevation_gain_m field on the candidate is a deliberately wrong
    # 999.0 (see make_candidate_with_geometry), so a result other than
    # "opposite ends" (1.0) proves the geometry/smoothing path was
    # actually used instead of the raw total.
    elevations = [0.0] * 46
    elevations[20] = 40.0  # isolated spike, smoothed away

    candidate = make_candidate_with_geometry(elevations)

    result = elevation_mismatch_norm(candidate, preferred_bucket="flat")

    assert result == pytest.approx(0.0)


def test_elevation_mismatch_norm_geometry_bucket_boundaries() -> None:
    # A steady, monotonic climb over the whole route -- smoothed gain
    # should land close to the raw total gain, so this exercises the
    # geometry path landing in the "hilly" bucket against a "flat"
    # preference (opposite ends -> 1.0), the same way the raw-total
    # equivalent test already does for the <2-point fallback.
    point_count = 46
    total_gain = 40.0  # over ~1000m -> 40 m/km, well past the 25 cutoff
    elevations = [
        total_gain * (index / (point_count - 1))
        for index in range(point_count)
    ]

    candidate = make_candidate_with_geometry(elevations)

    result = elevation_mismatch_norm(candidate, preferred_bucket="flat")

    assert result == pytest.approx(1.0)


def test_elevation_mismatch_norm_flat_preference_with_real_climb_floors_at_half() -> None:  # noqa: E501
    # A route whose net elevation change is ~0 (climbs back down to the
    # start), but contains one real sustained climb in the middle --
    # total/smoothed gain nets out "flat" per km, yet a "flat"
    # preference shouldn't score this as a 0.0 mismatch, since the
    # runner would still hit a real hill.
    climb_grade_pct = 5.0
    rise_per_point = STEP_M * (climb_grade_pct / 100.0)

    elevations = [0.0] * 5

    for _ in range(30):  # ~30*22=660m climb, comfortably over 200m/2%
        elevations.append(elevations[-1] + rise_per_point)

    for _ in range(30):  # descend back down to 0
        elevations.append(elevations[-1] - rise_per_point)

    elevations.extend([elevations[-1]] * 5)

    candidate = make_candidate_with_geometry(elevations)

    result = elevation_mismatch_norm(candidate, preferred_bucket="flat")

    assert result == pytest.approx(0.5)


def test_elevation_mismatch_norm_hilly_preference_without_climb_floors_at_half() -> None:  # noqa: E501
    # Rolling/jittery elevation that nets a "hilly" per-km total but
    # never sustains a single qualifying climb (small up/down wiggles,
    # each too short and/or too shallow to pass MIN_CLIMB_LENGTH_M /
    # MIN_CLIMB_GRADE_PCT) -- a "hilly" preference wants a real climb,
    # not noise that happens to sum to a big total.
    elevations = []
    for index in range(46):
        elevations.append(3.0 if index % 2 == 0 else 0.0)

    candidate = make_candidate_with_geometry(elevations)

    # Sanity: this jittery profile has no detected climbs, but does
    # bucket as "hilly" against a "flat" preference (i.e. its smoothed
    # per-km gain is nonzero) -- otherwise this test wouldn't isolate
    # the intended floor.
    assert detect_climbs(candidate.geometry) == []

    result = elevation_mismatch_norm(candidate, preferred_bucket="hilly")

    assert result == pytest.approx(0.5)


def test_off_route_norm_zero_at_route() -> None:
    assert off_route_norm(0.0) == pytest.approx(0.0)


def test_off_route_norm_scales_linearly_below_threshold() -> None:
    result = off_route_norm(RESTROOM_PROXIMITY_THRESHOLD_M / 2.0)

    assert result == pytest.approx(0.5)


def test_off_route_norm_clamps_at_one() -> None:
    result = off_route_norm(RESTROOM_PROXIMITY_THRESHOLD_M * 10.0)

    assert result == pytest.approx(1.0)


def test_normalize_min_max_empty_list() -> None:
    assert normalize_min_max([]) == []


def test_normalize_min_max_equal_values_all_zero() -> None:
    assert normalize_min_max([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_normalize_min_max_scales_to_unit_range() -> None:
    result = normalize_min_max([0.0, 50.0, 200.0])

    assert result == [
        pytest.approx(0.0),
        pytest.approx(0.25),
        pytest.approx(1.0),
    ]


def test_score_and_rank_candidates_splits_matched_and_fallback() -> None:
    # All three candidates share the same 3-point geometry shape (just
    # shifted far apart in longitude, so they never overlap for
    # similarity purposes), the same elevation_gain_m (so all fall in
    # the "flat" bucket), and restrooms with no hours/accessibility (so
    # restroom_confidence is identical for all three).
    candidate_a = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=2300.0,
    )
    candidate_b = make_candidate(
        start_lat=40.70,
        longitude=-73.90,
        distance_m=2100.0,
    )
    candidate_c = make_candidate(
        start_lat=40.70,
        longitude=-73.80,
        distance_m=2000.0,
    )

    restroom_a = make_restroom(
        source_id="a",
        latitude=40.71,
        longitude=-74.00,
    )
    restroom_b = make_restroom(
        source_id="b",
        latitude=40.71,
        longitude=-73.90,
    )
    restroom_c = make_restroom(
        source_id="c",
        latitude=40.70,
        longitude=-73.80,
    )

    result = score_and_rank_candidates(
        candidates=[
            candidate_a,
            candidate_b,
            candidate_c,
        ],
        restrooms=[
            restroom_a,
            restroom_b,
            restroom_c,
        ],
        target_distance_m=2000.0,
        min_mile_m=1000.0,
        max_mile_m=1200.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    # Raw errors:
    #   a: mile_range=0,    distance=300
    #   b: mile_range=0,    distance=100
    #   c: mile_range=1000, distance=0
    # Hard constraints: MAX_DISTANCE_ERROR_M=100, MAX_RESTROOM_RANGE_ERROR_M=500
    #   a: distance=300 > 100           -> fallback
    #   b: distance=100 <= 100, range=0 -> matched
    #   c: range=1000 > 500             -> fallback
    # Only b is matched, so it's alone in the matched pool (composite
    # ranking among a single candidate is trivially first). a and c are
    # both fallback, ranked by normalized distance+range error:
    # Batch-normalized (min-max over the 3 candidates, computed across
    # the full pool before the matched/fallback split):
    #   distance_norm: a=1.0, b=0.3333, c=0.0
    #   mile_norm:     a=0.0, b=0.0,    c=1.0
    # fallback composite = 0.5*distance_norm + 0.5*mile_norm
    #   a: 0.5*1.0 + 0.5*0.0 = 0.5
    #   c: 0.5*0.0 + 0.5*1.0 = 0.5
    # a and c tie on fallback composite_score (0.5 each); Python's sort
    # is stable, and a was appended to partial_scores before c, so a
    # sorts before c.
    assert [
        scored.candidate
        for scored in result
    ] == [
        candidate_b,
        candidate_a,
        candidate_c,
    ]

    scored_b, scored_a, scored_c = result

    assert scored_b.matched is True
    assert scored_a.matched is False
    assert scored_c.matched is False

    assert scored_a.distance_error_norm == pytest.approx(1.0)
    assert scored_b.distance_error_norm == pytest.approx(1 / 3)
    assert scored_c.distance_error_norm == pytest.approx(0.0)

    assert scored_a.mile_range_error_norm == pytest.approx(0.0)
    assert scored_b.mile_range_error_norm == pytest.approx(0.0)
    assert scored_c.mile_range_error_norm == pytest.approx(1.0)

    assert scored_a.composite_score == pytest.approx(0.5)
    assert scored_c.composite_score == pytest.approx(0.5)

    # b is the sole matched candidate: its composite score is the
    # renormalized 6-factor formula, and since elevation/repeated-
    # segment/interruption/similarity/off-route are all 0 (interruption
    # is 0 because the store passed in this test is empty, so
    # signals_per_km is 0 for every candidate) and restroom_confidence
    # is 0.5 (identical across all three restrooms), b's composite
    # collapses to just the restroom-confidence term. off_route_norm
    # is 0 because the matched restroom sits exactly on the route
    # (real match_restrooms_to_route(), not the make_match() fixture,
    # is used here via score_and_rank_candidates()).
    #   composite = (3/10)*0 + (2/10)*0 + (2/10)*0 + (1/10)*(1 - 0.5)
    #             + (1/10)*0 = (1/10)*0.5 = 1/20
    assert scored_b.composite_score == pytest.approx(1 / 20)
    assert scored_b.elevation_mismatch == pytest.approx(0.0)
    assert scored_b.repeated_segment_ratio == pytest.approx(0.0)
    assert scored_b.restroom_confidence == pytest.approx(0.5)
    assert scored_b.similarity_penalty == pytest.approx(0.0)


def test_score_and_rank_candidates_ranks_matched_pool_by_renormalized_composite() -> None:  # noqa: E501
    # Two candidates, both matched (small distance/range error), that
    # differ only in elevation_mismatch. This isolates the renormalized
    # composite among a purely-matched pool, verifying the 3/10 weight
    # on elevation_mismatch is applied as expected.
    flat_candidate = make_candidate_with_gain(
        elevation_gain_m=0.0,
        distance_m=1000.0,
    )
    hilly_candidate = make_candidate_with_gain(
        elevation_gain_m=40.0,
        distance_m=1000.0,
    )

    restroom = make_restroom(
        source_id="shared",
        latitude=40.70,
        longitude=-74.00,
    )

    result = score_and_rank_candidates(
        candidates=[hilly_candidate, flat_candidate],
        restrooms=[restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert [scored.candidate for scored in result] == [
        flat_candidate,
        hilly_candidate,
    ]

    for scored in result:
        assert scored.matched is True

    scored_flat, scored_hilly = result

    # flat_candidate: gain_per_km=0 -> "flat" bucket -> mismatch 0.0
    # hilly_candidate: gain_per_km=40 -> "hilly" bucket -> mismatch 1.0
    # Both restroom_confidence=0.5, repeated_segment_ratio=0, and
    # interruption_norm=0 (the store passed in this test is empty, so
    # signals_per_km is 0 for both). Both candidates share the single
    # restroom, sitting exactly on both candidates' single-point
    # geometry, so off_route_norm=0.0 for both. Both candidates use
    # make_candidate_with_gain()'s single-point geometry at the same
    # lat/lon, so they are a 100% grid-cell overlap: the provisional
    # order ranks flat first (lower subtotal), so hilly's
    # similarity_penalty against the already-ranked flat geometry is
    # 1.0 (full overlap), while flat -- ranked first, nothing above it
    # -- gets 0.0.
    #   flat:  (3/10)*0.0 + (2/10)*0.0 + (2/10)*0.0 + (1/10)*0.5
    #        + (1/10)*0.0 + (1/10)*0.0 = 1/20
    #   hilly: (3/10)*1.0 + (2/10)*0.0 + (2/10)*0.0 + (1/10)*0.5
    #        + (1/10)*0.0 + (1/10)*1.0
    #        = 3/10 + 1/20 + 1/10 = 6/20 + 1/20 + 2/20 = 9/20
    assert scored_flat.composite_score == pytest.approx(1 / 20)
    assert scored_hilly.composite_score == pytest.approx(9 / 20)
    assert scored_flat.similarity_penalty == pytest.approx(0.0)
    assert scored_hilly.similarity_penalty == pytest.approx(1.0)


def test_score_and_rank_candidates_off_route_term_affects_matched_composite() -> None:  # noqa: E501
    # Two candidates with identical shape/elevation/distance (just
    # shifted apart in longitude so each only ever matches its own
    # nearby restroom, and so they never overlap for similarity
    # purposes), differing only in how far their restroom sits from
    # the route -- isolates the new WEIGHT_OFF_ROUTE term in the
    # matched-pool composite.
    on_route_candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=1000.0,
    )
    off_route_candidate = make_candidate(
        start_lat=40.70,
        longitude=-73.90,
        distance_m=1000.0,
    )

    on_route_restroom = make_restroom(
        source_id="on-route",
        latitude=40.70,
        longitude=-74.00,
    )
    # ~65m north of off_route_candidate's first geometry point --
    # inside RESTROOM_PROXIMITY_THRESHOLD_M (130.0) so it still
    # matches, but roughly half the threshold away, giving a
    # non-trivial off_route_norm distinct from both 0.0 and 1.0.
    off_route_restroom = make_restroom(
        source_id="off-route",
        latitude=40.70 + (65.0 / 111_320.0),
        longitude=-73.90,
    )

    result = score_and_rank_candidates(
        candidates=[off_route_candidate, on_route_candidate],
        restrooms=[on_route_restroom, off_route_restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert [scored.candidate for scored in result] == [
        on_route_candidate,
        off_route_candidate,
    ]

    scored_on_route, scored_off_route = result

    assert scored_on_route.off_route_distance_m == pytest.approx(0.0)
    assert scored_off_route.off_route_distance_m > 0.0

    expected_off_route_norm = off_route_norm(
        scored_off_route.off_route_distance_m
    )

    # Both candidates are identical in shape, so their only diverging
    # composite input is off_route_norm -- similarity_penalty is 0.0
    # for both (there's no higher-ranked geometry above either, since
    # each is scored/ranked against a distinct restroom, not each
    # other's geometry) and every other factor matches.
    assert scored_off_route.composite_score - (
        scored_on_route.composite_score
    ) == pytest.approx(WEIGHT_OFF_ROUTE * expected_off_route_norm)


def test_score_and_rank_candidates_backfills_with_fallback_when_understocked() -> None:  # noqa: E501
    # One matched candidate and one fallback candidate. The plan's
    # design says scoring returns ALL matched+fallback candidates in
    # final order (matched first, then fallback) — main.py, not
    # scoring.py, is responsible for slicing to `count`. This test
    # confirms both pools are present and correctly ordered/labeled
    # even when the matched pool alone wouldn't fill a request.
    matched_candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=1000.0,
    )
    fallback_candidate = make_candidate(
        start_lat=40.70,
        longitude=-73.90,
        distance_m=5000.0,
    )

    restroom_near_matched = make_restroom(
        source_id="near-matched",
        latitude=40.71,
        longitude=-74.00,
    )
    restroom_near_fallback = make_restroom(
        source_id="near-fallback",
        latitude=40.71,
        longitude=-73.90,
    )

    result = score_and_rank_candidates(
        candidates=[fallback_candidate, matched_candidate],
        restrooms=[restroom_near_matched, restroom_near_fallback],
        target_distance_m=1000.0,
        min_mile_m=1000.0,
        max_mile_m=1200.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert len(result) == 2

    assert result[0].candidate == matched_candidate
    assert result[0].matched is True

    assert result[1].candidate == fallback_candidate
    assert result[1].matched is False


def test_score_and_rank_candidates_off_route_distance_m_surfaces_match_field() -> None:  # noqa: E501
    candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=1000.0,
    )
    restroom = make_restroom(
        source_id="only",
        latitude=40.70,
        longitude=-74.00,
    )

    result = score_and_rank_candidates(
        candidates=[candidate],
        restrooms=[restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert len(result) == 1
    # off_route_distance_m is a direct passthrough of
    # RestroomMatch.distance_to_route_m, which make_match() (and the
    # real match_restrooms_to_route()) sets from the restroom's
    # nearest-point distance to the route -- here it's the fixed 10.0
    # used by geo.py's real matching for a restroom placed on the route.
    assert result[0].off_route_distance_m == pytest.approx(
        result[0].restroom_match.distance_to_route_m
    )

def test_score_and_rank_candidates_lower_signals_per_km_outranks_higher() -> None:  # noqa: E501
    # Two candidates with identical shape/elevation/distance (shifted
    # apart in longitude so they never overlap for similarity purposes,
    # and so each only ever matches its own nearby restroom), differing
    # only in how many traffic signals sit near their route -- isolates
    # the new WEIGHT_INTERRUPTION term in the matched-pool composite.
    quiet_candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=1000.0,
    )
    noisy_candidate = make_candidate(
        start_lat=40.70,
        longitude=-73.90,
        distance_m=1000.0,
    )

    quiet_restroom = make_restroom(
        source_id="quiet",
        latitude=40.70,
        longitude=-74.00,
    )
    noisy_restroom = make_restroom(
        source_id="noisy",
        latitude=40.70,
        longitude=-73.90,
    )

    # Sits exactly on noisy_candidate's second geometry point (see
    # make_candidate()), well within the default 25m match threshold --
    # quiet_candidate's geometry is ~9km away in longitude, so it never
    # matches this signal. The cell index is built the same way
    # load_interruption_store() would, so route_interruptions()'s cell
    # lookup actually finds it.
    signals = (Coordinate(lat=40.71, lon=-73.90),)
    store = InterruptionStore(
        signals=signals,
        crossings=(),
        signal_cell_index=_build_cell_index(signals),
        crossing_cell_index={},
    )

    result = score_and_rank_candidates(
        candidates=[noisy_candidate, quiet_candidate],
        restrooms=[quiet_restroom, noisy_restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=store,
    )

    assert [scored.candidate for scored in result] == [
        quiet_candidate,
        noisy_candidate,
    ]

    scored_quiet, scored_noisy = result

    assert scored_quiet.signals_per_km == pytest.approx(0.0)
    assert scored_noisy.signals_per_km > 0.0
    assert scored_noisy.composite_score > scored_quiet.composite_score


def test_best_restroom_waypoint_returns_matched_restroom_coordinate() -> None:  # noqa: E501
    candidate = make_candidate(
        start_lat=40.70, longitude=-74.00, distance_m=2220.0
    )
    # Sits on the candidate's second geometry point -- well within the
    # proximity threshold, mile marker ~1112m.
    restroom = make_restroom(
        source_id="on-route", latitude=40.71, longitude=-74.00
    )

    waypoint = best_restroom_waypoint(
        candidate, [restroom], min_mile_m=800.0, max_mile_m=1600.0
    )

    assert waypoint == Coordinate(lat=40.71, lon=-74.00)


def test_score_and_rank_candidates_passes_through_shape_metrics() -> None:  # noqa: E501
    # No weight changes in this phase -- just confirms the three shape
    # fields make it onto ScoredCandidate unchanged from the standalone
    # shape.py functions, for the same candidate geometry.
    candidate = make_candidate(
        start_lat=40.70,
        longitude=-74.00,
        distance_m=2200.0,
    )
    restroom = make_restroom(
        source_id="shared",
        latitude=40.71,
        longitude=-74.00,
    )

    result = score_and_rank_candidates(
        candidates=[candidate],
        restrooms=[restroom],
        target_distance_m=2200.0,
        min_mile_m=0.0,
        max_mile_m=5000.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
    )

    assert len(result) == 1
    scored = result[0]

    assert scored.sharp_turn_count == sharp_turn_count(candidate.geometry)
    assert scored.u_turn_count == u_turn_count(candidate.geometry)
    assert scored.compactness == pytest.approx(
        compactness(candidate.geometry)
    )


def test_best_restroom_waypoint_returns_none_without_a_match() -> None:
    candidate = make_candidate(
        start_lat=40.70, longitude=-74.00, distance_m=2220.0
    )
    # ~0.1 degrees of longitude (~8.4km) off the route -- far beyond
    # the proximity threshold.
    restroom = make_restroom(
        source_id="far-away", latitude=40.71, longitude=-74.10
    )

    waypoint = best_restroom_waypoint(
        candidate, [restroom], min_mile_m=800.0, max_mile_m=1600.0
    )

    assert waypoint is None


# --- Workout presets (Phase F) ---------------------------------------


def test_weight_profiles_all_normalize_to_one() -> None:
    for profile in WEIGHT_PROFILES.values():
        total = (
            profile.elevation
            + profile.repeated_segment
            + profile.interruption
            + profile.similarity
            + profile.restroom_confidence
            + profile.off_route
            + profile.turns
            + profile.street
        )

        assert total == pytest.approx(1.0, abs=1e-9)


def test_unknown_workout_type_raises_value_error() -> None:
    candidate = make_candidate(
        start_lat=40.70, longitude=-74.00, distance_m=1000.0
    )
    restroom = make_restroom(
        source_id="shared", latitude=40.70, longitude=-74.00
    )

    with pytest.raises(ValueError):
        score_and_rank_candidates(
            candidates=[candidate],
            restrooms=[restroom],
            target_distance_m=1000.0,
            min_mile_m=0.0,
            max_mile_m=100.0,
            preferred_elevation_bucket="flat",
            interruption_store=EMPTY_INTERRUPTION_STORE,
            workout_type="marathon",
        )


def make_zigzag_candidate(
    longitude: float,
    distance_m: float = 333.0,
) -> RouteCandidate:
    # A 4-point staircase with two ~90-degree turns, each leg ~111m
    # (comfortably over shape.py's MIN_LEG_M=15m so each vertex counts
    # as a real turn, not vertex jitter). Flat elevation throughout, so
    # this isolates turn density from the elevation-mismatch factor.
    geometry = (
        RoutePoint(lat=40.70, lon=longitude, elevation_m=0.0),
        RoutePoint(lat=40.70, lon=longitude + 0.001, elevation_m=0.0),
        RoutePoint(
            lat=40.701, lon=longitude + 0.001, elevation_m=0.0
        ),
        RoutePoint(
            lat=40.701, lon=longitude + 0.002, elevation_m=0.0
        ),
    )

    return RouteCandidate(
        geometry=geometry,
        distance_m=distance_m,
        elevation_gain_m=0.0,
    )


def make_straight_candidate(
    longitude: float,
    distance_m: float = 333.0,
) -> RouteCandidate:
    # Same point count/spacing/elevation as make_zigzag_candidate, but
    # collinear -- zero turns, isolating turn density as the only
    # differing factor between the two.
    geometry = tuple(
        RoutePoint(
            lat=40.70,
            lon=longitude + 0.001 * index,
            elevation_m=0.0,
        )
        for index in range(4)
    )

    return RouteCandidate(
        geometry=geometry,
        distance_m=distance_m,
        elevation_gain_m=0.0,
    )


def test_intervals_preset_penalizes_turn_density_default_does_not() -> None:  # noqa: E501
    # Both candidates are flat (elevation_mismatch=0), matched to their
    # own nearby restroom (identical confidence, on-route), and never
    # overlap each other for similarity purposes (shifted apart in
    # longitude) -- the only differing composite input is turn density
    # (zigzag has 2 sharp turns, straight has 0).
    zigzag_candidate = make_zigzag_candidate(longitude=-74.00)
    straight_candidate = make_straight_candidate(longitude=-73.50)

    zigzag_restroom = make_restroom(
        source_id="zigzag", latitude=40.70, longitude=-74.00
    )
    straight_restroom = make_restroom(
        source_id="straight", latitude=40.70, longitude=-73.50
    )

    default_result = score_and_rank_candidates(
        candidates=[zigzag_candidate, straight_candidate],
        restrooms=[zigzag_restroom, straight_restroom],
        target_distance_m=333.0,
        min_mile_m=0.0,
        max_mile_m=1000.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type=None,
    )

    intervals_result = score_and_rank_candidates(
        candidates=[zigzag_candidate, straight_candidate],
        restrooms=[zigzag_restroom, straight_restroom],
        target_distance_m=333.0,
        min_mile_m=0.0,
        max_mile_m=1000.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type="intervals",
    )

    # Default weights don't score turn density at all (turns weight is
    # 0), so the two candidates tie on every scored factor and the sort
    # is stable -- zigzag (appended first) sorts first.
    assert default_result[0].candidate == zigzag_candidate

    # Under "intervals" (turns weight 3/12, heavily penalizing zigzag's
    # 2 sharp turns), the low-turn-density candidate that lost under
    # default weights now wins.
    assert intervals_result[0].candidate == straight_candidate


def test_long_preset_penalizes_low_restroom_confidence_more_than_default() -> None:  # noqa: E501
    # Two candidates, identical shape/elevation/distance (shifted apart
    # in longitude so they never overlap for similarity purposes and
    # each only matches its own restroom), differing only in restroom
    # confidence (one restroom has full metadata, the other has none).
    high_confidence_candidate = make_candidate(
        start_lat=40.70, longitude=-74.00, distance_m=1000.0
    )
    low_confidence_candidate = make_candidate(
        start_lat=40.70, longitude=-73.90, distance_m=1000.0
    )

    high_confidence_restroom = make_restroom(
        source_id="high-confidence",
        latitude=40.70,
        longitude=-74.00,
        hours_of_operation="8 AM-8 PM",
        accessibility="Accessible",
    )
    low_confidence_restroom = make_restroom(
        source_id="low-confidence",
        latitude=40.70,
        longitude=-73.90,
    )

    default_result = score_and_rank_candidates(
        candidates=[
            low_confidence_candidate,
            high_confidence_candidate,
        ],
        restrooms=[
            low_confidence_restroom,
            high_confidence_restroom,
        ],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type=None,
    )
    long_result = score_and_rank_candidates(
        candidates=[
            low_confidence_candidate,
            high_confidence_candidate,
        ],
        restrooms=[
            low_confidence_restroom,
            high_confidence_restroom,
        ],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type="long",
    )

    default_high = next(
        scored
        for scored in default_result
        if scored.candidate == high_confidence_candidate
    )
    default_low = next(
        scored
        for scored in default_result
        if scored.candidate == low_confidence_candidate
    )
    long_high = next(
        scored
        for scored in long_result
        if scored.candidate == high_confidence_candidate
    )
    long_low = next(
        scored
        for scored in long_result
        if scored.candidate == low_confidence_candidate
    )

    default_gap = default_low.composite_score - default_high.composite_score  # noqa: E501
    long_gap = long_low.composite_score - long_high.composite_score

    # "long"'s restroom_confidence weight (2/13 ≈ 0.154) is a larger
    # share of the composite than default's (1/10 = 0.1), so the
    # confidence penalty widens the gap between the two candidates.
    assert long_gap > default_gap


def test_hills_preset_weighs_elevation_more_heavily_than_default() -> None:
    flat_candidate = make_candidate_with_gain(
        elevation_gain_m=0.0, distance_m=1000.0
    )
    hilly_candidate = make_candidate_with_gain(
        elevation_gain_m=40.0, distance_m=1000.0
    )

    restroom = make_restroom(
        source_id="shared", latitude=40.70, longitude=-74.00
    )

    default_result = score_and_rank_candidates(
        candidates=[hilly_candidate, flat_candidate],
        restrooms=[restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type=None,
    )
    hills_result = score_and_rank_candidates(
        candidates=[hilly_candidate, flat_candidate],
        restrooms=[restroom],
        target_distance_m=1000.0,
        min_mile_m=0.0,
        max_mile_m=100.0,
        preferred_elevation_bucket="flat",
        interruption_store=EMPTY_INTERRUPTION_STORE,
        workout_type="hills",
    )

    default_hilly = next(
        scored
        for scored in default_result
        if scored.candidate == hilly_candidate
    )
    hills_hilly = next(
        scored
        for scored in hills_result
        if scored.candidate == hilly_candidate
    )

    # "hills"'s elevation weight (5/11 ≈ 0.455) is a larger share of
    # the composite than default's (3/10 = 0.3), so the mismatch
    # penalty on the hilly candidate (bucketed "hilly" against the
    # "flat" preference used here, mismatch=1.0) is larger in absolute
    # terms under "hills".
    assert hills_hilly.composite_score > default_hilly.composite_score
