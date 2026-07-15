import pytest

from app.restrooms.geo import RestroomMatch
from app.restrooms.models import Restroom
from app.restrooms.scoring import (
    best_match_for_range,
    elevation_bucket,
    elevation_mismatch_norm,
    mile_range_error_m,
    normalize_min_max,
    restroom_confidence,
    score_and_rank_candidates,
)
from app.routing.provider import RouteCandidate, RoutePoint


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


def test_score_and_rank_candidates_sorts_by_composite_score() -> None:
    # All three candidates share the same 3-point geometry shape (just
    # shifted far apart in longitude, so they never overlap for
    # similarity purposes), the same elevation_gain_m (so all fall in
    # the "flat" bucket), and restrooms with no hours/accessibility (so
    # restroom_confidence is identical for all three). That isolates
    # the ranking to distance_error and mile_range_error, batch-
    # normalized, which is what this test verifies by hand.
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

    # Raw errors (unchanged from the two-factor version):
    #   a: mile_range=0,    distance=300
    #   b: mile_range=0,    distance=100
    #   c: mile_range=1000, distance=0
    # Batch-normalized (min-max over the 3 candidates):
    #   distance_norm: a=1.0,     b=0.3333, c=0.0
    #   mile_norm:     a=0.0,     b=0.0,    c=1.0
    # elevation/repeated-segment/similarity are 0 for all three here,
    # and restroom_confidence (0.5) is identical for all three, so
    # composite_score = 0.35*distance_norm + 0.30*mile_norm + 0.025
    #   a: 0.35*1.0    + 0.30*0.0 + 0.025 = 0.375
    #   b: 0.35*0.3333 + 0.30*0.0 + 0.025 = 0.14167
    #   c: 0.35*0.0    + 0.30*1.0 + 0.025 = 0.325
    # -> ascending order: b, c, a
    assert [
        scored.candidate
        for scored in result
    ] == [
        candidate_b,
        candidate_c,
        candidate_a,
    ]

    for scored in result:
        assert scored.elevation_mismatch == pytest.approx(0.0)
        assert scored.repeated_segment_ratio == pytest.approx(0.0)
        assert scored.restroom_confidence == pytest.approx(0.5)
        assert scored.similarity_penalty == pytest.approx(0.0)

    scored_b, scored_c, scored_a = result

    assert scored_a.distance_error_norm == pytest.approx(1.0)
    assert scored_b.distance_error_norm == pytest.approx(1 / 3)
    assert scored_c.distance_error_norm == pytest.approx(0.0)

    assert scored_a.mile_range_error_norm == pytest.approx(0.0)
    assert scored_b.mile_range_error_norm == pytest.approx(0.0)
    assert scored_c.mile_range_error_norm == pytest.approx(1.0)

    assert scored_a.composite_score == pytest.approx(0.375)
    assert scored_b.composite_score == pytest.approx(0.14167, abs=1e-4)
    assert scored_c.composite_score == pytest.approx(0.325)