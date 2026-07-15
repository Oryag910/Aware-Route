from math import floor

from app.routing.provider import RoutePoint


GRID_CELL_SIZE_DEG = 0.0002
MIN_INDEX_GAP_FOR_REVISIT = 5
DEFAULT_CLOSURE_EXCLUSION_POINTS = 3


def grid_cell(
    point: RoutePoint,
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
) -> tuple[int, int]:
    return (
        floor(point.lat / cell_size_deg),
        floor(point.lon / cell_size_deg),
    )


def revisited_point_count(
    geometry: tuple[RoutePoint, ...],
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
    closure_exclusion_points: int = DEFAULT_CLOSURE_EXCLUSION_POINTS,
    min_index_gap: int = MIN_INDEX_GAP_FOR_REVISIT,
) -> int:
    start_index = closure_exclusion_points
    end_index = len(geometry) - closure_exclusion_points

    if start_index >= end_index:
        return 0

    first_index_by_cell: dict[tuple[int, int], int] = {}
    revisit_count = 0

    for index in range(start_index, end_index):
        cell = grid_cell(geometry[index], cell_size_deg)
        first_index = first_index_by_cell.get(cell)

        if first_index is None:
            first_index_by_cell[cell] = index
        elif index - first_index >= min_index_gap:
            revisit_count += 1

    return revisit_count


def repeated_segment_ratio(
    geometry: tuple[RoutePoint, ...],
    cell_size_deg: float = GRID_CELL_SIZE_DEG,
    closure_exclusion_points: int = DEFAULT_CLOSURE_EXCLUSION_POINTS,
    min_index_gap: int = MIN_INDEX_GAP_FOR_REVISIT,
) -> float:
    interior_point_count = (
        len(geometry) - 2 * closure_exclusion_points
    )

    if interior_point_count <= 0:
        return 0.0

    revisit_count = revisited_point_count(
        geometry,
        cell_size_deg=cell_size_deg,
        closure_exclusion_points=closure_exclusion_points,
        min_index_gap=min_index_gap,
    )

    return revisit_count / interior_point_count
