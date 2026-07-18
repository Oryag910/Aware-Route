from math import isclose

from app.generation.shape_metrics import isoperimetric_quotient
from app.routing.provider import RoutePoint


def _ring(coords: list[tuple[float, float]]) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat, lon=lon, elevation_m=0.0) for lat, lon in coords
    )


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
