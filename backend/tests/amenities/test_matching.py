from app.amenities.matching import (
    best_amenity_for_range,
    match_amenities_to_route,
)
from app.amenities.models import Amenity
from app.restrooms.geo import cumulative_distances_m
from app.routing.provider import RoutePoint


def _line(coords: list[tuple[float, float]]) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat, lon=lon, elevation_m=0.0) for lat, lon in coords
    )


def test_match_finds_near_amenity_and_drops_far() -> None:
    geometry = _line([(40.70, -74.00), (40.71, -74.00), (40.72, -74.00)])
    near = Amenity(lat=40.71, lon=-74.00, kind="fountain", name=None)  # on vertex 1
    far = Amenity(lat=40.90, lon=-74.00, kind="restroom", name="x")  # ~20km away

    matches = match_amenities_to_route(geometry, [near, far])

    assert len(matches) == 1
    assert matches[0].amenity is near
    assert matches[0].distance_to_route_m < 1.0
    # Nearest vertex is index 1, so the mile marker is the cumulative
    # distance to that vertex.
    expected_marker = cumulative_distances_m(geometry)[1]
    assert abs(matches[0].mile_marker_m - expected_marker) < 1.0


def test_match_empty_geometry_returns_empty() -> None:
    assert (
        match_amenities_to_route((), [Amenity(40.7, -74.0, "fountain", None)])
        == []
    )


def test_best_amenity_for_range_picks_closest_to_range() -> None:
    geometry = _line(
        [(40.70, -74.00), (40.71, -74.00), (40.72, -74.00), (40.73, -74.00)]
    )
    markers = cumulative_distances_m(geometry)
    early = Amenity(lat=40.71, lon=-74.00, kind="fountain", name=None)  # vertex 1
    late = Amenity(lat=40.73, lon=-74.00, kind="fountain", name=None)  # vertex 3

    matches = match_amenities_to_route(geometry, [early, late])
    # Range brackets the late vertex only.
    best = best_amenity_for_range(matches, markers[3] - 10.0, markers[3] + 10.0)

    assert best is not None
    assert best.amenity is late


def test_best_amenity_for_range_none_when_no_matches() -> None:
    assert best_amenity_for_range([], 0.0, 1000.0) is None
