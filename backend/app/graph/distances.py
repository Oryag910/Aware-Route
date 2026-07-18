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
