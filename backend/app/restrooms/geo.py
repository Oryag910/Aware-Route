from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from app.restrooms.models import Restroom
from app.routing.provider import Coordinate, RoutePoint


EARTH_RADIUS_M = 6_371_000.0

# Empirically tunable straight-line threshold; ignores barriers like rivers and park boundaries.
RESTROOM_PROXIMITY_THRESHOLD_M = 130.0


def haversine_m(a: Coordinate, b: Coordinate) -> float:
    lat_a = radians(a.lat)
    lon_a = radians(a.lon)
    lat_b = radians(b.lat)
    lon_b = radians(b.lon)

    latitude_difference = lat_b - lat_a
    longitude_difference = lon_b - lon_a

    haversine_value = (
        sin(latitude_difference / 2) ** 2
        + cos(lat_a)
        * cos(lat_b)
        * sin(longitude_difference / 2) ** 2
    )

    central_angle = 2 * asin(sqrt(haversine_value))

    return EARTH_RADIUS_M * central_angle


def cumulative_distances_m(
    geometry: tuple[RoutePoint, ...],
) -> list[float]:
    if not geometry:
        return []

    distances = [0.0]

    for index in range(1, len(geometry)):
        previous_point = geometry[index - 1]
        current_point = geometry[index]

        previous_coordinate = Coordinate(
            lat=previous_point.lat,
            lon=previous_point.lon,
        )

        current_coordinate = Coordinate(
            lat=current_point.lat,
            lon=current_point.lon,
        )

        segment_distance = haversine_m(
            previous_coordinate,
            current_coordinate,
        )

        distances.append(
            distances[-1] + segment_distance
        )

    return distances


@dataclass(frozen=True)
class RestroomMatch:
    restroom: Restroom
    distance_to_route_m: float
    mile_marker_m: float


def match_restrooms_to_route(
    geometry: tuple[RoutePoint, ...],
    restrooms: list[Restroom],
    max_distance_m: float = RESTROOM_PROXIMITY_THRESHOLD_M,
) -> list[RestroomMatch]:
    if not geometry:
        return []

    route_distances = cumulative_distances_m(geometry)

    route_coordinates = [
        Coordinate(
            lat=route_point.lat,
            lon=route_point.lon,
        )
        for route_point in geometry
    ]

    matches: list[RestroomMatch] = []

    for restroom in restrooms:
        restroom_coordinate = Coordinate(
            lat=restroom.latitude,
            lon=restroom.longitude,
        )

        minimum_distance = float("inf")
        nearest_route_index = 0

        for index, route_coordinate in enumerate(route_coordinates):
            distance = haversine_m(
                restroom_coordinate,
                route_coordinate,
            )

            if distance < minimum_distance:
                minimum_distance = distance
                nearest_route_index = index

        if minimum_distance <= max_distance_m:
            matches.append(
                RestroomMatch(
                    restroom=restroom,
                    distance_to_route_m=minimum_distance,
                    mile_marker_m=route_distances[
                        nearest_route_index
                    ],
                )
            )

    return matches
