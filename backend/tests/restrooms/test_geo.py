import pytest

from app.restrooms.geo import (
    cumulative_distances_m,
    haversine_m,
    match_restrooms_to_route,
)
from app.restrooms.models import Restroom
from app.routing.provider import Coordinate, RoutePoint


def make_restroom(
    source_id: str,
    latitude: float,
    longitude: float,
) -> Restroom:
    return Restroom(
        source_id=source_id,
        facility_name="Test Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=latitude,
        longitude=longitude,
    )


def test_cumulative_distances_m_empty() -> None:
    result = cumulative_distances_m(())

    assert result == []


def test_cumulative_distances_m_matches_haversine() -> None:
    geometry = (
        RoutePoint(
            lat=40.70,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.71,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.72,
            lon=-74.00,
            elevation_m=0.0,
        ),
    )

    result = cumulative_distances_m(geometry)

    first_leg_distance = haversine_m(
        Coordinate(
            lat=geometry[0].lat,
            lon=geometry[0].lon,
        ),
        Coordinate(
            lat=geometry[1].lat,
            lon=geometry[1].lon,
        ),
    )

    second_leg_distance = haversine_m(
        Coordinate(
            lat=geometry[1].lat,
            lon=geometry[1].lon,
        ),
        Coordinate(
            lat=geometry[2].lat,
            lon=geometry[2].lon,
        ),
    )

    assert result[0] == 0.0
    assert result[1] == pytest.approx(first_leg_distance)
    assert result[2] == pytest.approx(
        first_leg_distance + second_leg_distance
    )


def test_match_restrooms_to_route_includes_nearby() -> None:
    geometry = (
        RoutePoint(
            lat=40.70,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.71,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.72,
            lon=-74.00,
            elevation_m=0.0,
        ),
    )

    restroom = make_restroom(
        source_id="nearby-restroom",
        latitude=40.71,
        longitude=-73.9994,
    )

    result = match_restrooms_to_route(
        geometry,
        [restroom],
    )

    expected_distance_to_route = haversine_m(
        Coordinate(
            lat=restroom.latitude,
            lon=restroom.longitude,
        ),
        Coordinate(
            lat=geometry[1].lat,
            lon=geometry[1].lon,
        ),
    )

    cumulative_distances = cumulative_distances_m(geometry)

    assert len(result) == 1

    match = result[0]

    assert match.restroom == restroom
    assert match.distance_to_route_m == pytest.approx(
        expected_distance_to_route,
        abs=0.5,
    )
    assert match.mile_marker_m == pytest.approx(
        cumulative_distances[1]
    )
    assert 0.0 < match.mile_marker_m < cumulative_distances[-1]


def test_match_restrooms_to_route_excludes_far() -> None:
    geometry = (
        RoutePoint(
            lat=40.70,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.71,
            lon=-74.00,
            elevation_m=0.0,
        ),
        RoutePoint(
            lat=40.72,
            lon=-74.00,
            elevation_m=0.0,
        ),
    )

    restroom = make_restroom(
        source_id="far-restroom",
        latitude=40.71,
        longitude=-73.994,
    )

    result = match_restrooms_to_route(
        geometry,
        [restroom],
    )

    assert result == []
