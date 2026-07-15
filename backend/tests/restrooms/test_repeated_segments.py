import pytest

from app.restrooms.repeated_segments import (
    grid_cell,
    repeated_segment_ratio,
    revisited_point_count,
)
from app.routing.provider import RoutePoint


TEST_CELL_SIZE_DEG = 1.0


def make_point(lat: float, lon: float) -> RoutePoint:
    return RoutePoint(
        lat=lat,
        lon=lon,
        elevation_m=0.0,
    )


def point_in_cell(
    latitude_cell: int,
    longitude_cell: int,
) -> RoutePoint:
    return make_point(
        lat=latitude_cell + 0.25,
        lon=longitude_cell + 0.25,
    )


def geometry_from_cells(
    cells: tuple[tuple[int, int], ...],
) -> tuple[RoutePoint, ...]:
    return tuple(
        point_in_cell(latitude_cell, longitude_cell)
        for latitude_cell, longitude_cell in cells
    )


def test_grid_cell_groups_nearby_points() -> None:
    first = make_point(
        lat=40.70001,
        lon=-73.99999,
    )
    nearby = make_point(
        lat=40.70015,
        lon=-73.99985,
    )
    different_cell = make_point(
        lat=40.70021,
        lon=-73.99985,
    )

    assert grid_cell(first) == grid_cell(nearby)
    assert grid_cell(first) != grid_cell(different_cell)


def test_repeated_segment_ratio_clean_loop_is_zero() -> None:
    geometry = geometry_from_cells(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
            (4, 0),
            (5, 0),
            (5, 1),
            (5, 2),
            (5, 3),
            (5, 4),
            (5, 5),
            (4, 5),
            (3, 5),
            (2, 5),
            (1, 5),
            (0, 5),
            (0, 4),
            (0, 3),
            (0, 2),
            (0, 1),
            (0, 0),
        )
    )

    ratio = repeated_segment_ratio(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert ratio == 0.0


def test_revisited_point_count_detects_out_and_back_route() -> None:
    outbound_cells = tuple(
        (index, 0)
        for index in range(10)
    )

    geometry = geometry_from_cells(
        outbound_cells + tuple(reversed(outbound_cells))
    )

    revisit_count = revisited_point_count(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )
    ratio = repeated_segment_ratio(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert revisit_count > 0
    assert ratio > 0.0


def test_revisited_point_count_matches_expected_value() -> None:
    geometry = geometry_from_cells(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
            (4, 0),
            (5, 0),
            (6, 0),
            (5, 0),
            (4, 0),
            (3, 0),
            (2, 0),
            (1, 0),
            (0, 0),
        )
    )

    revisit_count = revisited_point_count(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
        closure_exclusion_points=0,
    )
    ratio = repeated_segment_ratio(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
        closure_exclusion_points=0,
    )

    assert revisit_count == 4
    assert ratio == pytest.approx(4 / 13)


def test_repeated_segment_ratio_closure_only_is_zero() -> None:
    geometry = geometry_from_cells(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (2, 1),
            (2, 2),
            (1, 2),
            (0, 2),
            (0, 1),
            (0, 0),
        )
    )

    assert (
        repeated_segment_ratio(
            geometry,
            cell_size_deg=TEST_CELL_SIZE_DEG,
        )
        == 0.0
    )


def test_repeated_segment_ratio_empty_geometry_is_zero() -> None:
    assert repeated_segment_ratio(()) == 0.0
