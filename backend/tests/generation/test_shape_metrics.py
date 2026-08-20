from math import cos, isclose, radians, sin

from app.generation.shape_metrics import (
    _M_PER_DEG_LAT,
    _M_PER_DEG_LON,
    elongation_ratio,
    isoperimetric_quotient,
    max_start_distance_m,
    radial_exposure,
)
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


def _ring(coords: list[tuple[float, float]]) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat, lon=lon, elevation_m=0.0) for lat, lon in coords
    )


_BASE_LAT = 40.750
_BASE_LON = -73.980
_LON_SCALE = _M_PER_DEG_LON * cos(radians(_BASE_LAT))


def _rect_ring(half_width_m: float, half_height_m: float, rotation_deg: float = 0.0) -> tuple[RoutePoint, ...]:
    """A rectangle centered near _BASE_LAT/_BASE_LON, built in local
    meters and rotated by `rotation_deg` about its center before being
    projected back to lat/lon via the exact inverse of shape_metrics'
    own equirectangular projection -- so a "rotated" rectangle really
    is the same shape in meter-space, just reoriented."""
    theta = radians(rotation_deg)
    cos_t, sin_t = cos(theta), sin(theta)

    local_corners = [
        (-half_width_m, -half_height_m),
        (half_width_m, -half_height_m),
        (half_width_m, half_height_m),
        (-half_width_m, half_height_m),
    ]

    coords = []
    for x, y in local_corners:
        rx = x * cos_t - y * sin_t
        ry = x * sin_t + y * cos_t
        lat = _BASE_LAT + ry / _M_PER_DEG_LAT
        lon = _BASE_LON + rx / _LON_SCALE
        coords.append((lat, lon))

    return _ring(coords)


def test_square_scores_higher_than_thin_sliver() -> None:
    # ~a square around a Manhattan-ish latitude.
    square = _ring(
        [
            (40.750, -73.980),
            (40.750, -73.979),
            (40.751, -73.979),
            (40.751, -73.980),
        ]
    )
    # Same span east-west but almost no north-south extent.
    sliver = _ring(
        [
            (40.750, -73.980),
            (40.750, -73.979),
            (40.75001, -73.979),
            (40.75001, -73.980),
        ]
    )

    assert isoperimetric_quotient(square) > isoperimetric_quotient(sliver)


def test_square_quotient_near_pi_over_4() -> None:
    square = _ring(
        [
            (40.750, -73.980),
            (40.750, -73.979),
            (40.751, -73.979),
            (40.751, -73.980),
        ]
    )
    # A perfect square's isoperimetric quotient is pi/4 ~= 0.785.
    assert isclose(isoperimetric_quotient(square), 0.785, abs_tol=0.02)


def test_quotient_in_unit_interval() -> None:
    square = _ring(
        [
            (40.750, -73.980),
            (40.750, -73.979),
            (40.751, -73.979),
            (40.751, -73.980),
        ]
    )
    quotient = isoperimetric_quotient(square)
    assert 0.0 <= quotient <= 1.0


def test_degenerate_geometry_returns_zero() -> None:
    assert isoperimetric_quotient(()) == 0.0
    single = _ring([(40.75, -73.98)])
    assert isoperimetric_quotient(single) == 0.0


# -- elongation_ratio -------------------------------------------------


def test_square_less_elongated_than_thin_rectangle() -> None:
    square = _rect_ring(50.0, 50.0)
    thin_rectangle = _rect_ring(200.0, 20.0)

    assert elongation_ratio(square) < elongation_ratio(thin_rectangle)


def test_square_elongation_near_one() -> None:
    square = _rect_ring(50.0, 50.0)
    assert isclose(elongation_ratio(square), 1.0, abs_tol=0.02)


def test_rotated_rectangle_has_approximately_same_elongation() -> None:
    flat = _rect_ring(200.0, 20.0, rotation_deg=0.0)
    rotated_30 = _rect_ring(200.0, 20.0, rotation_deg=30.0)
    rotated_90 = _rect_ring(200.0, 20.0, rotation_deg=90.0)
    rotated_137 = _rect_ring(200.0, 20.0, rotation_deg=137.0)

    baseline = elongation_ratio(flat)
    for rotated in (rotated_30, rotated_90, rotated_137):
        assert isclose(elongation_ratio(rotated), baseline, rel_tol=0.05)


def test_thin_sliver_more_elongated_than_square() -> None:
    square = _rect_ring(50.0, 50.0)
    sliver = _rect_ring(300.0, 5.0)

    assert elongation_ratio(sliver) > elongation_ratio(square) * 5


def test_straight_line_like_route_is_highly_elongated() -> None:
    # An out-and-back-like path: travels out along one line, then
    # returns along the same line -- every point is collinear.
    outbound = [(40.750, -73.980 - i * 0.0001) for i in range(20)]
    inbound = list(reversed(outbound[:-1]))
    line_route = _ring(outbound + inbound)

    square = _rect_ring(50.0, 50.0)

    assert elongation_ratio(line_route) > elongation_ratio(square) * 10


def test_elongation_degenerate_geometry_returns_neutral_one() -> None:
    assert elongation_ratio(()) == 1.0
    assert elongation_ratio(_ring([(40.75, -73.98)])) == 1.0
    # All points coincide -- zero spread in every direction.
    coincident = _ring([(40.75, -73.98)] * 5)
    assert elongation_ratio(coincident) == 1.0


# -- max_start_distance_m ----------------------------------------------


def test_max_start_distance_picks_farthest_point_not_last() -> None:
    start = (40.750, -73.980)
    near = (40.7505, -73.980)
    far = (40.760, -73.980)
    back_near_start = (40.7502, -73.980)

    route = _ring([start, near, far, back_near_start])

    expected = max(
        haversine_m(Coordinate(*start), Coordinate(*point))
        for point in (near, far, back_near_start)
    )
    assert isclose(max_start_distance_m(route), expected, rel_tol=1e-9)


def test_max_start_distance_degenerate_geometry_returns_zero() -> None:
    assert max_start_distance_m(()) == 0.0
    assert max_start_distance_m(_ring([(40.75, -73.98)])) == 0.0


# -- radial_exposure -----------------------------------------------------


def test_radial_exposure_matches_ratio() -> None:
    route = _ring([(40.750, -73.980), (40.760, -73.980)])
    expected_max_dist = max_start_distance_m(route)

    assert isclose(radial_exposure(route, 2000.0), expected_max_dist / 2000.0, rel_tol=1e-9)


def test_radial_exposure_out_and_back_near_half() -> None:
    # An idealized out-and-back: straight out then straight back,
    # total traveled distance ~= 2x the max start distance.
    outbound = [(40.750, -73.980 - i * 0.001) for i in range(11)]
    inbound = list(reversed(outbound[:-1]))
    route = _ring(outbound + inbound)

    max_dist = max_start_distance_m(route)
    traveled = max_dist * 2.0

    assert isclose(radial_exposure(route, traveled), 0.5, abs_tol=0.02)


def test_radial_exposure_nonpositive_distance_returns_zero() -> None:
    route = _ring([(40.750, -73.980), (40.760, -73.980)])
    assert radial_exposure(route, 0.0) == 0.0
    assert radial_exposure(route, -100.0) == 0.0
