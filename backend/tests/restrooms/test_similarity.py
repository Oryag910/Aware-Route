import pytest

from app.restrooms.similarity import (
    route_cell_set,
    route_overlap_ratio,
    similarity_penalty_for_candidate,
)
from app.routing.provider import RoutePoint


TEST_CELL_SIZE_DEG = 1.0


def make_point(
    latitude_cell: int,
    longitude_cell: int,
) -> RoutePoint:
    return RoutePoint(
        lat=latitude_cell + 0.25,
        lon=longitude_cell + 0.25,
        elevation_m=0.0,
    )


def make_geometry(
    cells: tuple[tuple[int, int], ...],
) -> tuple[RoutePoint, ...]:
    return tuple(
        make_point(latitude_cell, longitude_cell)
        for latitude_cell, longitude_cell in cells
    )


def test_route_cell_set_returns_unique_cells() -> None:
    geometry = make_geometry(
        (
            (0, 0),
            (1, 0),
            (1, 0),
            (2, 0),
        )
    )

    cells = route_cell_set(
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert cells == {
        (0, 0),
        (1, 0),
        (2, 0),
    }


def test_route_overlap_ratio_identical_routes_is_one() -> None:
    geometry = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )

    ratio = route_overlap_ratio(
        geometry,
        geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert ratio == 1.0


def test_route_overlap_ratio_disjoint_routes_is_zero() -> None:
    geometry_a = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
        )
    )
    geometry_b = make_geometry(
        (
            (10, 0),
            (11, 0),
            (12, 0),
        )
    )

    ratio = route_overlap_ratio(
        geometry_a,
        geometry_b,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert ratio == 0.0


def test_route_overlap_ratio_partial_overlap() -> None:
    geometry_a = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )
    geometry_b = make_geometry(
        (
            (2, 0),
            (3, 0),
            (4, 0),
        )
    )

    ratio = route_overlap_ratio(
        geometry_a,
        geometry_b,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert ratio == pytest.approx(0.5)


def test_route_overlap_ratio_is_asymmetric() -> None:
    short_geometry = make_geometry(
        (
            (0, 0),
            (1, 0),
        )
    )
    long_geometry = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )

    short_overlap = route_overlap_ratio(
        short_geometry,
        long_geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )
    long_overlap = route_overlap_ratio(
        long_geometry,
        short_geometry,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert short_overlap == 1.0
    assert long_overlap == 0.5


def test_route_overlap_ratio_empty_first_route_is_zero() -> None:
    geometry_b = make_geometry(
        (
            (0, 0),
            (1, 0),
        )
    )

    ratio = route_overlap_ratio(
        (),
        geometry_b,
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert ratio == 0.0


def test_similarity_penalty_is_order_dependent() -> None:
    geometry_a = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )
    geometry_b = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )
    geometry_c = make_geometry(
        (
            (10, 0),
            (11, 0),
            (12, 0),
            (13, 0),
        )
    )

    first_candidate_penalty = similarity_penalty_for_candidate(
        geometry_a,
        higher_ranked_geometries=[],
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )
    duplicate_candidate_penalty = similarity_penalty_for_candidate(
        geometry_b,
        higher_ranked_geometries=[geometry_a],
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )
    disjoint_candidate_penalty = similarity_penalty_for_candidate(
        geometry_c,
        higher_ranked_geometries=[geometry_a, geometry_b],
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert first_candidate_penalty == 0.0
    assert duplicate_candidate_penalty == 1.0
    assert disjoint_candidate_penalty == 0.0


def test_similarity_penalty_uses_maximum_overlap() -> None:
    candidate = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
        )
    )
    partially_overlapping = make_geometry(
        (
            (0, 0),
        )
    )
    mostly_overlapping = make_geometry(
        (
            (0, 0),
            (1, 0),
            (2, 0),
        )
    )

    penalty = similarity_penalty_for_candidate(
        candidate,
        higher_ranked_geometries=[
            partially_overlapping,
            mostly_overlapping,
        ],
        cell_size_deg=TEST_CELL_SIZE_DEG,
    )

    assert penalty == 0.75