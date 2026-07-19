from typing import Any

import networkx as nx
import osmnx as ox

from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate


def nearest_node(graph: Any, coord: Coordinate) -> int:
    return int(ox.distance.nearest_nodes(graph, X=coord.lon, Y=coord.lat))


def single_source_distances(graph: Any, source: int) -> dict[int, float]:
    result: dict[int, float] = nx.single_source_dijkstra_path_length(
        graph, source, weight="length"
    )
    return result


def single_source_paths(
    graph: Any, source: int
) -> tuple[dict[int, float], dict[int, list[int]]]:
    """One Dijkstra from `source` returning both the distance map and the
    shortest node path to every reachable node.

    Generation issues many outbound legs from the same start (per
    turnaround, per amenity, per spur, and re-issued on every length-tuning
    iteration). Computing them all up front here turns each into an O(1)
    dict lookup via `outbound_path`, instead of a separate bidirectional
    Dijkstra -- the dominant cost on a CPU-constrained host.
    """
    distances, paths = nx.single_source_dijkstra(graph, source, weight="length")
    return distances, paths


def shortest_path(graph: Any, source: int, target: int) -> list[int]:
    try:
        path: list[int] = nx.shortest_path(
            graph, source, target, weight="length"
        )
    except nx.NetworkXNoPath as error:
        raise RouteNotFoundError(
            f"no path between node {source} and node {target}"
        ) from error

    return path


def outbound_path(
    graph: Any,
    source: int,
    target: int,
    paths: dict[int, list[int]] | None = None,
) -> list[int]:
    """Shortest path source -> target, served from a precomputed
    single-source `paths` map when available (falls back to a fresh
    `shortest_path` otherwise). Raises `RouteNotFoundError` if the target
    is unreachable, matching `shortest_path`."""
    if paths is not None:
        path = paths.get(target)
        if path is None:
            raise RouteNotFoundError(
                f"no path between node {source} and node {target}"
            )
        return path
    return shortest_path(graph, source, target)
