from app.restrooms.repeated_segments import (
    GRID_CELL_SIZE_DEG,
    grid_cell,
)
from app.routing.provider import RoutePoint


def route_cell_set(
    geometry: tuple[RoutePoint, ...],
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
) -> set[tuple[int, int]]:
    return {
        grid_cell(point, cell_size_deg)
        for point in geometry
    }


def route_overlap_ratio(
    geometry_a: tuple[RoutePoint, ...],
    geometry_b: tuple[RoutePoint, ...],
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
) -> float:
    cells_a = route_cell_set(geometry_a, cell_size_deg)

    if not cells_a:
        return 0.0

    cells_b = route_cell_set(geometry_b, cell_size_deg)
    overlapping_cells = cells_a & cells_b

    return len(overlapping_cells) / len(cells_a)


def similarity_penalty_for_candidate(
    geometry: tuple[RoutePoint, ...],
    higher_ranked_geometries: list[tuple[RoutePoint, ...]],
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
) -> float:
    if not higher_ranked_geometries:
        return 0.0

    return max(
        route_overlap_ratio(
            geometry,
            other_geometry,
            cell_size_deg,
        )
        for other_geometry in higher_ranked_geometries
    )