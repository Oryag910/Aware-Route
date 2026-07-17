from math import asin, atan2, cos, degrees, radians, sin, sqrt

from app.routing.provider import Coordinate


EARTH_RADIUS_M = 6_371_000.0


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


def bearing_deg(origin: Coordinate, destination: Coordinate) -> float:
    lat1 = radians(origin.lat)
    lat2 = radians(destination.lat)
    delta_lon = radians(destination.lon - origin.lon)

    x = sin(delta_lon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon)

    return degrees(atan2(x, y)) % 360.0


def destination_point(
    origin: Coordinate,
    bearing: float,
    distance_m: float,
) -> Coordinate:
    angular_distance = distance_m / EARTH_RADIUS_M
    bearing_rad = radians(bearing)

    lat1 = radians(origin.lat)
    lon1 = radians(origin.lon)

    lat2 = asin(
        sin(lat1) * cos(angular_distance)
        + cos(lat1) * sin(angular_distance) * cos(bearing_rad)
    )
    lon2 = lon1 + atan2(
        sin(bearing_rad) * sin(angular_distance) * cos(lat1),
        cos(angular_distance) - sin(lat1) * sin(lat2),
    )

    return Coordinate(lat=degrees(lat2), lon=degrees(lon2))
