from math import cos, pi, radians, sin

import pytest

from app.flow.shape import compactness, sharp_turn_count, u_turn_count
from app.routing.provider import RoutePoint


def make_line(count: int) -> tuple[RoutePoint, ...]:
    # Straight north-south line, points ~111m apart -- well above
    # MIN_LEG_M, so every accumulated leg is exactly one hop.
    return tuple(
        RoutePoint(lat=40.70 + 0.001 * index, lon=-74.00, elevation_m=0.0)
        for index in range(count)
    )


def make_right_angle_grid() -> tuple[RoutePoint, ...]:
    # Walks east, then north, then west, then south -- four legs joined
    # by three 90-degree corners (sharp turns), each leg long enough to
    # clear MIN_LEG_M.
    return (
        RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.70, lon=-73.997, elevation_m=0.0),
        RoutePoint(lat=40.703, lon=-73.997, elevation_m=0.0),
        RoutePoint(lat=40.703, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.706, lon=-74.00, elevation_m=0.0),
    )


def make_out_and_back() -> tuple[RoutePoint, ...]:
    # Straight out, then straight back along the same line -- a 180
    # degree reversal at the turnaround point.
    return (
        RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.701, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.702, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.701, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
    )


def make_regular_polygon(sides: int, radius_m: float = 200.0) -> tuple[RoutePoint, ...]:  # noqa: E501
    center_lat = 40.70
    center_lon = -74.00
    degree_to_m = (2 * pi * 6_371_000.0) / 360.0
    lon_scale = cos(radians(center_lat))

    points = []

    for index in range(sides):
        angle = 2 * pi * index / sides
        dx_m = radius_m * cos(angle)
        dy_m = radius_m * sin(angle)

        points.append(
            RoutePoint(
                lat=center_lat + dy_m / degree_to_m,
                lon=center_lon + dx_m / (degree_to_m * lon_scale),
                elevation_m=0.0,
            )
        )

    # Close the loop back to the first point.
    points.append(points[0])

    return tuple(points)


def make_square_loop(side_m: float = 300.0) -> tuple[RoutePoint, ...]:
    center_lat = 40.70
    center_lon = -74.00
    degree_to_m = (2 * pi * 6_371_000.0) / 360.0
    lon_scale = cos(radians(center_lat))

    half = side_m / 2.0

    corners_m = [
        (-half, -half),
        (half, -half),
        (half, half),
        (-half, half),
        (-half, -half),
    ]

    return tuple(
        RoutePoint(
            lat=center_lat + dy_m / degree_to_m,
            lon=center_lon + dx_m / (degree_to_m * lon_scale),
            elevation_m=0.0,
        )
        for dx_m, dy_m in corners_m
    )


def test_straight_line_has_no_turns() -> None:
    geometry = make_line(6)

    assert sharp_turn_count(geometry) == 0
    assert u_turn_count(geometry) == 0


def test_right_angle_grid_counts_sharp_turns() -> None:
    geometry = make_right_angle_grid()

    assert sharp_turn_count(geometry) == 3
    assert u_turn_count(geometry) == 0


def test_out_and_back_has_u_turn_and_low_compactness() -> None:
    geometry = make_out_and_back()

    assert u_turn_count(geometry) >= 1
    assert compactness(geometry) == pytest.approx(0.0, abs=0.05)


def test_regular_polygon_is_highly_compact() -> None:
    geometry = make_regular_polygon(16)

    assert compactness(geometry) > 0.9


def test_square_loop_compactness_matches_pi_over_four() -> None:
    geometry = make_square_loop()

    assert compactness(geometry) == pytest.approx(pi / 4, abs=0.05)


def test_degenerate_two_point_geometry_is_all_zero() -> None:
    geometry = (
        RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
        RoutePoint(lat=40.701, lon=-74.00, elevation_m=0.0),
    )

    assert sharp_turn_count(geometry) == 0
    assert u_turn_count(geometry) == 0
    assert compactness(geometry) == pytest.approx(0.0)
