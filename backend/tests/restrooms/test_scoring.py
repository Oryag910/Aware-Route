import pytest

from app.restrooms.geo import RESTROOM_PROXIMITY_THRESHOLD_M, RestroomMatch
from app.restrooms.models import Restroom
from app.restrooms.scoring import (
    WEIGHT_OFF_ROUTE,
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
    # renormalized 5-factor formula, and since elevation/repeated-
    # segment/similarity/off-route are all 0 and restroom_confidence
    # is 0.5 (identical across all three restrooms), b's composite
    # collapses to just the restroom-confidence term. off_route_norm
    # is 0 because the matched restroom sits exactly on the route
    # (real match_restrooms_to_route(), not the make_match() fixture,
    # is used here via score_and_rank_candidates()).
    #   composite = (3/8)*0 + (2/8)*0 + (1/8)*0 + (1/8)*(1 - 0.5)
    #             + (1/8)*0 = (1/8)*0.5 = 1/16
    assert scored_b.composite_score == pytest.approx(1 / 16)
    assert scored_b.elevation_mismatch == pytest.approx(0.0)
    assert scored_b.repeated_segment_ratio == pytest.approx(0.0)
    assert scored_b.restroom_confidence == pytest.approx(0.5)
    assert scored_b.similarity_penalty == pytest.approx(0.0)


def test_score_and_rank_candidates_ranks_matched_pool_by_renormalized_composite() -> None:  # noqa: E501
    # Two candidates, both matched (small distance/range error), that
    # differ only in elevation_mismatch. This isolates the renormalized
    # composite among a purely-matched pool, verifying the 3/8 weight
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
    # Both restroom_confidence=0.5, repeated_segment_ratio=0. Both
    # candidates share the single restroom, sitting exactly on both
    # candidates' single-point geometry, so off_route_norm=0.0 for
    # both. Both candidates use make_candidate_with_gain()'s single-
    # point geometry at the same lat/lon, so they are a 100% grid-cell
    # overlap: the provisional order ranks flat first (lower subtotal),
    # so hilly's similarity_penalty against the already-ranked flat
    # geometry is 1.0 (full overlap), while flat -- ranked first,
    # nothing above it -- gets 0.0.
    #   flat:  (3/8)*0.0 + (1/8)*0.5 + (1/8)*0.0 + (1/8)*0.0 = 1/16
    #   hilly: (3/8)*1.0 + (1/8)*0.5 + (1/8)*1.0 + (1/8)*0.0
    #        = 3/8 + 1/16 + 1/8 = 6/16 + 1/16 + 2/16 = 9/16
    assert scored_flat.composite_score == pytest.approx(1 / 16)
    assert scored_hilly.composite_score == pytest.approx(9 / 16)
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
