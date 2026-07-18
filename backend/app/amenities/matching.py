"""Match amenities (restrooms + fountains) to a route's geometry.

Generalizes `app/restrooms/geo.py`'s restroom-to-route matching so any
`Amenity` (restroom or fountain) can be matched uniformly: nearest-vertex
distance to the route plus the along-route arc-length (mile marker) of
that nearest vertex. Straight-line only, same 130m proximity threshold and
same barrier caveat as the restroom matcher (ignores rivers / park fences).
"""

from dataclasses import dataclass

from app.amenities.models import Amenity
from app.restrooms.geo import (
    RESTROOM_PROXIMITY_THRESHOLD_M,
    cumulative_distances_m,
)
from app.restrooms.scoring import mile_range_error_m
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


# Reuse the restroom threshold so restrooms and fountains are held to the
# same "on the route" bar.
AMENITY_PROXIMITY_THRESHOLD_M = RESTROOM_PROXIMITY_THRESHOLD_M


@dataclass(frozen=True)
class AmenityMatch:
    amenity: Amenity
    distance_to_route_m: float
    mile_marker_m: float


def match_amenities_to_route(
    geometry: tuple[RoutePoint, ...],
    amenities: list[Amenity],
    max_distance_m: float = AMENITY_PROXIMITY_THRESHOLD_M,
) -> list[AmenityMatch]:
    if not geometry:
        return []

    route_distances = cumulative_distances_m(geometry)
    route_coordinates = [
        Coordinate(lat=point.lat, lon=point.lon) for point in geometry
    ]

    matches: list[AmenityMatch] = []

    for amenity in amenities:
        amenity_coordinate = Coordinate(lat=amenity.lat, lon=amenity.lon)

        minimum_distance = float("inf")
        nearest_route_index = 0

        for index, route_coordinate in enumerate(route_coordinates):
            distance = haversine_m(amenity_coordinate, route_coordinate)
            if distance < minimum_distance:
                minimum_distance = distance
                nearest_route_index = index

        if minimum_distance <= max_distance_m:
            matches.append(
                AmenityMatch(
                    amenity=amenity,
                    distance_to_route_m=minimum_distance,
                    mile_marker_m=route_distances[nearest_route_index],
                )
            )

    return matches


def best_amenity_for_range(
    matches: list[AmenityMatch],
    min_mile_m: float,
    max_mile_m: float,
) -> AmenityMatch | None:
    """The matched amenity closest to the requested along-route range."""
    if not matches:
        return None

    return min(
        matches,
        key=lambda match: mile_range_error_m(
            match.mile_marker_m, min_mile_m, max_mile_m
        ),
    )
