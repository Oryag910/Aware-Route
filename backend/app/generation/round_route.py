from typing import Any

import networkx as nx

from app.generation.shape_metrics import isoperimetric_quotient
from app.generation.turnarounds import select_turnarounds
from app.graph.distances import (
    nearest_node,
    shortest_path,
    single_source_distances,
)
from app.graph.model import path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate


# How much dearer a reused outbound edge looks to the return-leg
# Dijkstra. High enough to push the return onto genuinely different
# streets, low enough that no alternative still loses to backtracking.
REUSE_PENALTY = 4.0


def _reuse_penalty_weight(outbound_pairs: set[frozenset[int]]) -> Any:
    """Build a networkx callable edge weight that inflates the cost of
    any edge whose endpoints were used on the outbound leg."""

    def weight(u: int, v: int, edge_dict: dict[Any, dict[str, Any]]) -> float:
        base = float(min(data["length"] for data in edge_dict.values()))
        if frozenset((u, v)) in outbound_pairs:
            return base * REUSE_PENALTY
        return base

    return weight


def _return_path(
    graph: Any, turnaround: int, start_node: int, outbound: list[int]
) -> list[int] | None:
    """Shortest path turnaround -> start that avoids reusing outbound
    edges where an alternative exists. None if unreachable."""
    outbound_pairs = {
        frozenset((u, v)) for u, v in zip(outbound, outbound[1:])
    }
    try:
        path: list[int] = nx.shortest_path(
            graph,
            turnaround,
            start_node,
            weight=_reuse_penalty_weight(outbound_pairs),
        )
    except nx.NetworkXNoPath:
        return None
    return path


def round_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
) -> list[tuple[RouteCandidate, list[int]]]:
    """(candidate, loop_node_path) pairs ranked roundest first.

    Shared by the public generator and the length tuner. For each
    bearing-diverse turnaround the outbound leg is the shortest path out
    and the return leg is a reuse-penalised Dijkstra preferring fresh
    streets. Ranked by isoperimetric quotient (roundest first)."""
    target_radius_m = (target_distance_m / 2.0) * radius_scale
    turnarounds = select_turnarounds(
        graph,
        start_node,
        dists,
        target_radius_m,
        count * 2,
        prefer_straight=False,
    )

    scored: list[tuple[float, RouteCandidate, list[int]]] = []

    for turnaround, _dist in turnarounds:
        try:
            outbound = shortest_path(graph, start_node, turnaround)
        except RouteNotFoundError:
            continue

        return_path = _return_path(graph, turnaround, start_node, outbound)
        if return_path is None:
            continue

        loop = outbound + return_path[1:]
        candidate = path_to_candidate(graph, loop)
        quotient = isoperimetric_quotient(candidate.geometry)
        scored.append((quotient, candidate, loop))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [(candidate, loop) for _, candidate, loop in scored[:count]]


def generate_round(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
) -> list[RouteCandidate]:
    """Loop routes: out to a turnaround, back a different way.

    Ranked roundest first by isoperimetric quotient. The turnaround
    radius is ~target/2 scaled by `radius_scale`.
    """
    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)
    pairs = round_pairs(
        graph, start_node, dists, target_distance_m, count, radius_scale
    )
    return [candidate for candidate, _ in pairs]
