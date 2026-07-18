from math import cos, pi, radians

from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


# Equirectangular projection constants (meters per degree). Longitude
# scale is corrected by cos(lat0) at the geometry's mean latitude.
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LON = 111_320.0


def isoperimetric_quotient(geometry: tuple[RoutePoint, ...]) -> float:
    """Compactness of a route's polygon, 4*pi*A / P**2.

    Ranges 0..1, with 1.0 for a perfect circle and small values for
    thin, out-and-back-like shapes. Area A is the shoelace area of the
    lon/lat points projected to local meters (equirectangular around
    the mean latitude); perimeter P is the summed haversine length of
    consecutive segments. Returns 0.0 when the perimeter is degenerate.
    """
    if len(geometry) < 3:
        return 0.0

    mean_lat = sum(point.lat for point in geometry) / len(geometry)
    lon_scale = _M_PER_DEG_LON * cos(radians(mean_lat))

    projected = [
        (point.lon * lon_scale, point.lat * _M_PER_DEG_LAT)
        for point in geometry
    ]

    # Shoelace area over the closed ring (wrap last -> first).
    twice_area = 0.0
    for (x0, y0), (x1, y1) in zip(projected, projected[1:] + projected[:1]):
        twice_area += x0 * y1 - x1 * y0
    area = abs(twice_area) / 2.0

    # Close the ring (wrap last -> first) so the perimeter spans the
    # same polygon the shoelace area does; otherwise an open geometry
    # under-counts one side and the quotient can exceed 1.
    ring = list(geometry) + [geometry[0]]
    perimeter = 0.0
    for start, end in zip(ring, ring[1:]):
        perimeter += haversine_m(
            Coordinate(lat=start.lat, lon=start.lon),
            Coordinate(lat=end.lat, lon=end.lon),
        )

    if perimeter == 0.0:
        return 0.0

    return 4.0 * pi * area / perimeter**2
