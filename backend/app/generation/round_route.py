from typing import Any

from app.generation.reuse_penalty import reuse_penalized_return_path
from app.generation.shape_metrics import isoperimetric_quotient
from app.generation.turnarounds import select_turnarounds
from app.graph.distances import (
    nearest_node,
    outbound_path,
    single_source_distances,
)
from app.graph.model import path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate


# Upper bound on how many turnarounds actually get a full return-leg
# Dijkstra (`reuse_penalized_return_path`) per call, independent of how
# large `count` grows for an overcomplete candidate pool. Without this,
# an overcomplete pool request (e.g. facility-free routing asking for a
# raw pool wider than the final result count) would multiply real Dijkstra
# searches by `count * 2` on every radius-scale tuning iteration -- the
# turnaround fix (see app.generation.turnarounds) means that multiplier
# is no longer throttled by accidental sector starvation, so it needs an
# explicit ceiling to keep latency bounded.
MAX_TURNAROUND_ATTEMPTS = 10


def round_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
    paths: dict[int, list[int]] | None = None,
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
        min(count * 2, MAX_TURNAROUND_ATTEMPTS),
        prefer_straight=False,
    )

    scored: list[tuple[float, RouteCandidate, list[int]]] = []

    for turnaround, _dist in turnarounds:
        try:
            outbound = outbound_path(graph, start_node, turnaround, paths)
        except RouteNotFoundError:
            continue

        return_path = reuse_penalized_return_path(
            graph, turnaround, start_node, outbound
        )
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
