from typing import Any, Literal

import networkx as nx

from app.amenities.snapping import SnappedAmenity, amenities_in_range
from app.generation.length_tune import tune_pair_to_target
from app.generation.shape_metrics import isoperimetric_quotient
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
    """Round loop with `amenity_node` as its far point: walk out to the
    amenity, then return by a reuse-penalised path that prefers streets
    not already walked, so the two legs enclose real area instead of
    doubling back. The residual to target length is absorbed by the
    tuner.

    The old approach walked to the amenity then bolted on a tiny
    ``target/2 - amenity_dist`` loop at the far end, which for a typical
    (amenity near the half-target radius) request left almost no radius
    for a real loop and produced a straight there-and-back with a nub.
    Treating the amenity as the loop's turnaround instead keeps the full
    radius available for enclosing area.
    """
    try:
        outbound = shortest_path(graph, start_node, amenity_node)
    except RouteNotFoundError:
        return None

    return_path = _return_path(graph, amenity_node, start_node, outbound)
    if return_path is None:
        return None

    loop = outbound + return_path[1:]
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
    QualityMetrics. "mix" keeps both sub-shapes it can build and lets the
    shape-aware ranking below order them (rounder first), so a mix request
    surfaces genuine loops rather than collapsing to the out-and-back that
    merely happened to fit the target distance best.
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

        triples.extend(produced)

    triples.sort(key=_ranking_key(shape, target_distance_m))

    return _dedup_triples(triples)[:count]


def _ranking_key(
    shape: Shape, target_distance_m: float
) -> Any:
    """Order amenity-passing triples for the requested shape.

    "out_and_back" cares only about hitting the target length. "round"
    and "mix" prefer rounder routes first (by isoperimetric quotient),
    breaking ties by distance error -- this is what surfaces genuine
    loops ahead of the out-and-backs that fit distance better but ignore
    the requested shape.
    """

    def key(
        triple: tuple[RouteCandidate, list[int], Shape],
    ) -> tuple[float, float]:
        candidate = triple[0]
        distance_error = abs(candidate.distance_m - target_distance_m)
        if shape == "out_and_back":
            return (0.0, distance_error)
        return (-isoperimetric_quotient(candidate.geometry), distance_error)

    return key


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
