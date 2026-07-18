from typing import Any

from app.generation.turnarounds import select_turnarounds
from app.graph.distances import shortest_path, single_source_distances
from app.graph.model import node_coordinate, path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.geometry import bearing_deg
from app.routing.provider import Coordinate, RouteCandidate


def out_and_back_path(graph: Any, start_node: int, turnaround: int) -> list[int]:
    """Node path that walks start -> turnaround and retraces back."""
    path = shortest_path(graph, start_node, turnaround)
    return path + list(reversed(path))[1:]


def out_and_back_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
) -> list[tuple[RouteCandidate, list[int]]]:
    """(candidate, node_path) out-and-back pairs, most-diverse-by-bearing
    first. Shared by the public generator and the length tuner so both
    see the underlying node paths (needed for spur splicing)."""
    target_radius_m = (target_distance_m / 2.0) * radius_scale
    turnarounds = select_turnarounds(
        graph,
        start_node,
        dists,
        target_radius_m,
        count,
        prefer_straight=True,
    )

    start_coord = node_coordinate(graph, start_node)
    with_bearing: list[tuple[float, RouteCandidate, list[int]]] = []

    for turnaround, _dist in turnarounds:
        try:
            full = out_and_back_path(graph, start_node, turnaround)
        except RouteNotFoundError:
            continue

        candidate = path_to_candidate(graph, full)
        bearing = bearing_deg(start_coord, node_coordinate(graph, turnaround))
        with_bearing.append((bearing, candidate, full))

    ordered = _diverse_by_bearing(with_bearing)
    return [(candidate, path) for _, candidate, path in ordered][:count]


def generate_out_and_back(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
) -> list[RouteCandidate]:
    """Out-and-back routes: run out to a turnaround, retrace the way home.

    The turnaround sits at ~target/2 (each leg is walked twice), scaled
    by `radius_scale`. High straightness is preferred so each route hugs
    a near-straight line out; results are ordered most-diverse-by-bearing.
    """
    from app.graph.distances import nearest_node

    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)
    pairs = out_and_back_pairs(
        graph, start_node, dists, target_distance_m, count, radius_scale
    )
    return [candidate for candidate, _ in pairs]


def _diverse_by_bearing(
    with_bearing: list[tuple[float, RouteCandidate, list[int]]],
) -> list[tuple[float, RouteCandidate, list[int]]]:
    """Greedily reorder candidates to maximise angular spread of their
    turnaround bearings."""
    if not with_bearing:
        return []

    remaining = list(with_bearing)
    ordered = [remaining.pop(0)]

    while remaining:
        chosen_bearings = [bearing for bearing, _, _ in ordered]
        best_index = 0
        best_gap = -1.0

        for index, (bearing, _, _) in enumerate(remaining):
            gap = min(
                _angular_distance(bearing, other) for other in chosen_bearings
            )
            if gap > best_gap:
                best_gap = gap
                best_index = index

        ordered.append(remaining.pop(best_index))

    return ordered


def _angular_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)
