import pytest

from app.restrooms.geo import RestroomMatch
from app.restrooms.models import Restroom
from app.restrooms.scoring import (
    best_match_for_range,
    mile_range_error_m,
    score_and_rank_candidates,
)
from app.routing.provider import RouteCandidate, RoutePoint


def make_restroom(
    source_id: str,
    latitude: float,
    longitude: float,
) -> Restroom:
    return Restroom(
        source_id=source_id,
        facility_name=f"Restroom {source_id}",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
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
    )

    assert result == []


def test_score_and_rank_candidates_sorts_by_range_then_distance() -> None:
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
    )

    assert [
        scored.candidate
        for scored in result
    ] == [
        candidate_b,
        candidate_a,
        candidate_c,
    ]

    assert result[0].mile_range_error_m == pytest.approx(0.0)
    assert result[0].distance_error_m == pytest.approx(100.0)

    assert result[1].mile_range_error_m == pytest.approx(0.0)
    assert result[1].distance_error_m == pytest.approx(300.0)

    assert result[2].mile_range_error_m == pytest.approx(1000.0)
    assert result[2].distance_error_m == pytest.approx(0.0)