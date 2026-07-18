from typing import Any, Literal

import networkx as nx

from app.amenities.snapping import SnappedAmenity, amenities_in_range
from app.generation.length_tune import tune_pair_to_target
from app.generation.turnarounds import select_turnarounds
from app.graph.distances import (
    nearest_node,
    shortest_path,
    single_source_distances,
)
from app.graph.model import path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate


Shape = Literal["round", "out_and_back", "mix"]

# How many best-ranked in-range amenities to try generating a route
# through before giving up. Wider than `count` so a few generation
# failures (unreachable turnaround, etc.) still leave enough candidates.
CANDIDATE_MULTIPLIER = 2

# Mirrors round_route.py's reuse-penalty technique so the return leg of
# an amenity-first loop takes different streets than the outbound leg.
REUSE_PENALTY = 4.0


def _reuse_penalty_weight(outbound_pairs: set[frozenset[int]]) -> Any:
    def weight(u: int, v: int, edge_dict: dict[Any, dict[str, Any]]) -> float:
        base = float(min(data["length"] for data in edge_dict.values()))
        if frozenset((u, v)) in outbound_pairs:
            return base * REUSE_PENALTY
        return base

    return weight


def _return_path(
    graph: Any, turnaround: int, start_node: int, outbound: list[int]
) -> list[int] | None:
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


def _out_and_back_through_pair(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    amenity_node: int,
    target_distance_m: float,
) -> tuple[RouteCandidate, list[int]] | None:
    """Out-and-back through `amenity_node`: walk out to it and retrace
    home, then spur the residual to hit target length. The amenity sits
    at arc-distance = its (sub-target/2) range, so the doubled path
    undershoots and the spur fills the rest -- the amenity ends up on
    the route at the requested range."""
    try:
        path = shortest_path(graph, start_node, amenity_node)
    except RouteNotFoundError:
        return None

    out_and_back = path + list(reversed(path))[1:]
    candidate = path_to_candidate(graph, out_and_back)

    return tune_pair_to_target(
        graph, start_node, candidate, out_and_back, target_distance_m, dists=dists
    )


def _round_through_pair(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    amenity_node: int,
    target_distance_m: float,
) -> tuple[RouteCandidate, list[int]] | None:
    """Round route through `amenity_node`: walk out to it, continue to a
    turnaround at the remaining half-target radius, then return via a
    reuse-penalised path preferring streets not already walked."""
    try:
        outbound_to_amenity = shortest_path(graph, start_node, amenity_node)
    except RouteNotFoundError:
        return None

    amenity_dist = dists.get(amenity_node)
    if amenity_dist is None:
        return None

    remaining_radius_m = (target_distance_m / 2.0) - amenity_dist
    if remaining_radius_m <= 0:
        return None

    dists_from_amenity = single_source_distances(graph, amenity_node)
    turnarounds = select_turnarounds(
        graph,
        amenity_node,
        dists_from_amenity,
        remaining_radius_m,
        count=1,
        prefer_straight=False,
    )
    if not turnarounds:
        return None

    turnaround_node, _dist = turnarounds[0]

    try:
        amenity_to_turnaround = shortest_path(
            graph, amenity_node, turnaround_node
        )
    except RouteNotFoundError:
        return None

    outbound_full = outbound_to_amenity + amenity_to_turnaround[1:]

    return_path = _return_path(
        graph, turnaround_node, start_node, outbound_full
    )
    if return_path is None:
        return None

    loop = outbound_full + return_path[1:]
    candidate = path_to_candidate(graph, loop)

    return tune_pair_to_target(
        graph, start_node, candidate, loop, target_distance_m, dists=dists
    )


def through_amenities_pairs(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
    shape: Shape,
    count: int,
) -> list[tuple[RouteCandidate, list[int], Shape]]:
    """(candidate, node_path, shape) triples for amenity-passing routes.

    Same selection/ranking logic as `generate_through_amenities`, but
    also returns each candidate's node_path and the concrete shape
    ("round"/"out_and_back") that produced it, needed by
    `app.generation.engine.generate_routes` to compute node-path-based
    QualityMetrics. "mix" picks whichever of the two sub-shapes best
    hits the target, same as the candidate-only path.
    """
    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)

    in_range = amenities_in_range(snapped, dists, min_range_m, max_range_m)
    pool = in_range[: count * CANDIDATE_MULTIPLIER]

    triples: list[tuple[RouteCandidate, list[int], Shape]] = []

    for entry in pool:
        amenity_node = entry.node_id
        produced: list[tuple[RouteCandidate, list[int], Shape]] = []

        if shape in ("out_and_back", "mix"):
            oab = _out_and_back_through_pair(
                graph, start_node, dists, amenity_node, target_distance_m
            )
            if oab is not None:
                produced.append((oab[0], oab[1], "out_and_back"))

        if shape in ("round", "mix"):
            loop = _round_through_pair(
                graph, start_node, dists, amenity_node, target_distance_m
            )
            if loop is not None:
                produced.append((loop[0], loop[1], "round"))

        if not produced:
            continue

        if shape == "mix":
            best = min(
                produced,
                key=lambda t: abs(t[0].distance_m - target_distance_m),
            )
            triples.append(best)
        else:
            triples.extend(produced)

    triples.sort(
        key=lambda triple: abs(triple[0].distance_m - target_distance_m)
    )

    return _dedup_triples(triples)[:count]


def generate_through_amenities(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
    shape: Shape,
    count: int,
) -> list[RouteCandidate]:
    """Route candidates that pass an amenity within [min_range_m,
    max_range_m] of the start (by graph arc-distance).

    Ranks in-range amenities by closeness to the range midpoint and
    tries to build a route through each until `count` candidates exist
    or the pool is exhausted. Candidates are ordered by distance error
    to `target_distance_m`.
    """
    triples = through_amenities_pairs(
        graph, start, target_distance_m, snapped, min_range_m, max_range_m, shape, count
    )
    return [candidate for candidate, _node_path, _shape in triples]


def _dedup_triples(
    triples: list[tuple[RouteCandidate, list[int], Shape]],
) -> list[tuple[RouteCandidate, list[int], Shape]]:
    seen: set[tuple[tuple[float, float], ...]] = set()
    unique: list[tuple[RouteCandidate, list[int], Shape]] = []

    for candidate, node_path, shape in triples:
        key = tuple((point.lat, point.lon) for point in candidate.geometry)
        if key in seen:
            continue
        seen.add(key)
        unique.append((candidate, node_path, shape))

    return unique
