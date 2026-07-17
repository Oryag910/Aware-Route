from math import cos, pi, radians

from app.routing.geometry import EARTH_RADIUS_M, bearing_deg, haversine_m
from app.routing.provider import Coordinate, RoutePoint


# ORS foot-walking geometry vertices are spaced ~19m apart -- accumulating
# hops until this minimum length collapses that vertex jitter before a
# heading change is measured, so a straight sidewalk isn't reported as a
# string of tiny "turns".
MIN_LEG_M = 15.0

# Turns strictly above this angle (degrees) are "sharp" -- e.g. a street
# corner rather than a gentle bend.
SHARP_TURN_DEG = 60.0

# Turns strictly above this angle (degrees) are U-turns -- doubling back
# on yourself, the hallmark of an out-and-back route.
U_TURN_DEG = 150.0


def _as_coordinate(point: RoutePoint) -> Coordinate:
    return Coordinate(lat=point.lat, lon=point.lon)


def turn_angles(geometry: tuple[RoutePoint, ...]) -> list[float]:
    """Absolute heading change (0-180 degrees) at each vertex reached by
    walking the geometry and accumulating segments until a hop of at
    least MIN_LEG_M is covered -- collapses ORS's densely-spaced
    vertices into legs long enough for a heading to be meaningful."""
    if len(geometry) < 3:
        return []

    coordinates = [_as_coordinate(point) for point in geometry]

    legs: list[tuple[Coordinate, Coordinate]] = []
    leg_start_index = 0
    accumulated_m = 0.0

    for index in range(len(coordinates) - 1):
        accumulated_m += haversine_m(
            coordinates[index], coordinates[index + 1]
        )

        if accumulated_m >= MIN_LEG_M:
            legs.append(
                (coordinates[leg_start_index], coordinates[index + 1])
            )
            leg_start_index = index + 1
            accumulated_m = 0.0

    if len(legs) < 2:
        return []

    angles: list[float] = []

    for previous_leg, next_leg in zip(legs, legs[1:]):
        incoming_bearing = bearing_deg(previous_leg[0], previous_leg[1])
        outgoing_bearing = bearing_deg(next_leg[0], next_leg[1])

        raw_difference = abs(outgoing_bearing - incoming_bearing) % 360.0
        turn_angle = min(raw_difference, 360.0 - raw_difference)

        angles.append(turn_angle)

    return angles


def sharp_turn_count(geometry: tuple[RoutePoint, ...]) -> int:
    return sum(
        1
        for angle in turn_angles(geometry)
        if SHARP_TURN_DEG < angle <= U_TURN_DEG
    )


def u_turn_count(geometry: tuple[RoutePoint, ...]) -> int:
    return sum(
        1 for angle in turn_angles(geometry) if angle > U_TURN_DEG
    )


def _project_to_meters(
    geometry: tuple[RoutePoint, ...],
) -> list[tuple[float, float]]:
    """Equirectangular projection of each point to local meters around
    the geometry's mean latitude -- good enough at running-route scale
    (a few km) for a shoelace-formula area estimate."""
    mean_lat = sum(point.lat for point in geometry) / len(geometry)
    mean_lat_rad = radians(mean_lat)
    lon_scale = cos(mean_lat_rad)

    degree_to_m = (2 * pi * EARTH_RADIUS_M) / 360.0

    return [
        (
            point.lon * degree_to_m * lon_scale,
            point.lat * degree_to_m,
        )
        for point in geometry
    ]


def compactness(geometry: tuple[RoutePoint, ...]) -> float:
    """Isoperimetric quotient 4*pi*A/P^2, in [0, 1] -- 1.0 for a
    circle, near 0 for a thin sliver like an out-and-back route."""
    if len(geometry) < 3:
        return 0.0

    points_m = _project_to_meters(geometry)

    area = 0.0
    perimeter = 0.0

    for index in range(len(points_m)):
        x1, y1 = points_m[index]
        x2, y2 = points_m[(index + 1) % len(points_m)]

        area += x1 * y2 - x2 * y1
        perimeter += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    area = abs(area) / 2.0

    if perimeter <= 0.0:
        return 0.0

    return (4 * pi * area) / (perimeter ** 2)
