from math import cos, pi, radians, sqrt

from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


# Equirectangular projection constants (meters per degree). Longitude
# scale is corrected by cos(lat0) at the geometry's mean latitude.
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LON = 111_320.0


def _project_local_meters(geometry: tuple[RoutePoint, ...]) -> list[tuple[float, float]]:
    """Local equirectangular projection of geometry to (x, y) meters,
    centered on the geometry's mean latitude. Same projection used by
    `isoperimetric_quotient` -- shared here so metrics agree on what
    "meters" means for a given route."""
    mean_lat = sum(point.lat for point in geometry) / len(geometry)
    lon_scale = _M_PER_DEG_LON * cos(radians(mean_lat))
    return [(point.lon * lon_scale, point.lat * _M_PER_DEG_LAT) for point in geometry]


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

    projected = _project_local_meters(geometry)

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


def max_start_distance_m(geometry: tuple[RoutePoint, ...]) -> float:
    """Farthest straight-line (haversine) distance from geometry[0] to
    any other point on the route.

    Measures how far a runner ever gets from their starting point --
    distinct from route length, which measures distance traveled along
    the path. Returns 0.0 for empty or single-point geometry.
    """
    if len(geometry) < 2:
        return 0.0

    start = Coordinate(lat=geometry[0].lat, lon=geometry[0].lon)
    return max(
        haversine_m(start, Coordinate(lat=point.lat, lon=point.lon))
        for point in geometry[1:]
    )


def radial_exposure(geometry: tuple[RoutePoint, ...], route_distance_m: float) -> float:
    """max_start_distance_m / route_distance_m.

    `route_distance_m` is the route's actual traveled distance (e.g.
    `RouteCandidate.distance_m`), NOT a geometry-derived perimeter --
    callers with an authoritative traveled distance should always pass
    it explicitly rather than approximating it from geometry. A route
    that runs straight out 5km and straight back on a 10km run scores
    ~0.5; a broad loop that never strays far from home scores much
    lower for the same total distance. Returns 0.0 for degenerate
    geometry or a non-positive route distance.
    """
    if route_distance_m <= 0.0:
        return 0.0
    return max_start_distance_m(geometry) / route_distance_m


def elongation_ratio(geometry: tuple[RoutePoint, ...]) -> float:
    """Rotation-invariant elongation of the route's footprint via PCA.

    Projects the geometry to local meters (same equirectangular
    projection as `isoperimetric_quotient`), computes the 2x2
    covariance matrix of the point cloud, and returns
    sqrt(major_eigenvalue / minor_eigenvalue) -- the ratio of spread
    along the shape's principal axes. This is the aspect ratio of the
    point cloud's best-fit ellipse, so it does not depend on how the
    route happens to be oriented on the map (rotation invariant),
    unlike a plain lat/lon bounding box.

    ~1.0 = balanced/square-like (spread is roughly equal in every
    direction), larger = increasingly elongated/linear. An
    out-and-back route -- points clustered along one line -- pushes
    this arbitrarily high.

    Returns 1.0 (neutral/undefined -- there's no shape to measure) for
    fewer than 2 points or when all points coincide. Exactly-collinear
    geometry floors the minor eigenvalue at a small fraction of the
    major eigenvalue so the ratio stays a large finite number instead
    of dividing by zero.
    """
    if len(geometry) < 2:
        return 1.0

    points = _project_local_meters(geometry)
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n

    var_x = sum((x - mean_x) ** 2 for x, _ in points) / n
    var_y = sum((y - mean_y) ** 2 for _, y in points) / n
    covar_xy = sum((x - mean_x) * (y - mean_y) for x, y in points) / n

    # Closed-form eigenvalues of the 2x2 symmetric covariance matrix.
    trace = var_x + var_y
    determinant = var_x * var_y - covar_xy**2
    discriminant = max(trace**2 - 4.0 * determinant, 0.0)  # guard fp noise near 0
    sqrt_discriminant = sqrt(discriminant)
    major = (trace + sqrt_discriminant) / 2.0
    minor = (trace - sqrt_discriminant) / 2.0

    if major <= 0.0:
        return 1.0  # all points coincide -- no spread in any direction

    minor = max(minor, major * 1e-9)
    return sqrt(major / minor)
