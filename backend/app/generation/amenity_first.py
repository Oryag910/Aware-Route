from dataclasses import dataclass
from typing import Any, Literal

import networkx as nx

from app.amenities.snapping import SnappedAmenity
from app.generation.length_tune import (
    DEFAULT_TOLERANCE_M,
    _spur_path,
    tune_pair_to_target,
)
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import (
    nearest_node,
    outbound_path,
    single_source_paths,
)
from app.graph.model import path_distance_m, path_to_candidate
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

# Reuse penalties tried, roundest (most-avoiding of the outbound streets)
# first. A heavier penalty forces a fresher -- and therefore longer --
# return leg, so when the roundest loop overshoots the target the builder
# steps down to a lighter penalty (a shorter return) until it fits, trading
# a little roundness for hitting the distance. Penalty 1.0 is the plain
# shortest path back (a there-and-back), which always fits.
_RETURN_PENALTIES = (REUSE_PENALTY, 2.5, 1.6, 1.0)


def _reuse_penalty_weight(
    outbound_pairs: set[frozenset[int]], penalty: float = REUSE_PENALTY
) -> Any:
    def weight(u: int, v: int, edge_dict: dict[Any, dict[str, Any]]) -> float:
        base = float(min(data["length"] for data in edge_dict.values()))
        if frozenset((u, v)) in outbound_pairs:
            return base * penalty
        return base

    return weight


def _return_path(
    graph: Any,
    turnaround: int,
    start_node: int,
    outbound: list[int],
    penalty: float = REUSE_PENALTY,
) -> list[int] | None:
    outbound_pairs = {
        frozenset((u, v)) for u, v in zip(outbound, outbound[1:])
    }
    try:
        path: list[int] = nx.shortest_path(
            graph,
            turnaround,
            start_node,
            weight=_reuse_penalty_weight(outbound_pairs, penalty),
        )
    except nx.NetworkXNoPath:
        return None
    return path


@dataclass(frozen=True)
class _Placement:
    """A way to put one amenity on a route at a requested cumulative
    along-route position.

    `d` is the amenity's shortest graph distance from the start;
    `cumulative_target` is where along the finished route the runner passes
    it; `on_return` says whether that happens on the way back (only
    out-and-backs pass a point twice)."""

    entry: SnappedAmenity
    d: float
    cumulative_target: float
    on_return: bool


def _select_placements(
    snapped: list[SnappedAmenity],
    dists: dict[int, float],
    target_distance_m: float,
    min_range_m: float,
    max_range_m: float,
    shape: Shape,
    count: int,
) -> list[_Placement]:
    """Amenity placements whose *cumulative along-route* position lands in
    [min_range_m, max_range_m].

    The requested range is the distance the runner has covered when they
    reach the amenity -- measured along the path, not straight-line from the
    start. On an out-and-back a point at shortest-distance ``d`` is passed
    twice: outbound at cumulative ``d`` and again on the way back at
    ``target - d``. So a restroom only ~2 mi from home can legitimately
    satisfy a "restroom at mile 7-8" ask on a 10 mi run (you pass it at mile
    2 on the way out and mile 8 on the way back). ``d`` must be <= half the
    target -- you can't reach a point more than half a there-and-back away.
    Round loops don't retrace, so only the outbound position is offered for
    them; "mix" gets both and lets ranking choose.
    """
    half = target_distance_m / 2.0 + DEFAULT_TOLERANCE_M
    midpoint = (min_range_m + max_range_m) / 2.0
    placements: list[_Placement] = []

    for entry in snapped:
        d = dists.get(entry.node_id)
        if d is None or d <= 0.0 or d > half:
            continue
        if min_range_m <= d <= max_range_m:
            placements.append(_Placement(entry, d, d, on_return=False))
        if shape in ("out_and_back", "mix"):
            back = target_distance_m - d
            if min_range_m <= back <= max_range_m:
                placements.append(_Placement(entry, d, back, on_return=True))

    placements.sort(key=lambda p: abs(p.cumulative_target - midpoint))
    return placements[: count * CANDIDATE_MULTIPLIER]


def _out_and_back_placed(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    amenity_node: int,
    d: float,
    target_distance_m: float,
    on_return: bool,
    paths: dict[int, list[int]] | None = None,
) -> tuple[RouteCandidate, list[int]] | None:
    """Out-and-back to `amenity_node`, padded to `target_distance_m`, with
    the amenity landing on the requested leg.

    The bare there-and-back is ``2*d``; the residual is absorbed by an
    out-and-back spur from the start. Appending that spur *after* the fold
    leaves the amenity at cumulative ~``d`` (outbound); prepending it pushes
    the amenity to cumulative ~``target-d`` (the return leg). Either way the
    total lands on target and the amenity sits where the range asked."""
    try:
        out = outbound_path(graph, start_node, amenity_node, paths)
    except RouteNotFoundError:
        return None

    base = out + list(reversed(out))[1:]
    deficit = target_distance_m - path_distance_m(graph, base)

    if deficit <= DEFAULT_TOLERANCE_M:
        # Already at/over target: the plain there-and-back is the best fit;
        # the amenity sits at the fold (~half) and a spur can't shorten it.
        node_path = base
    else:
        spur = _spur_path(graph, start_node, dists, deficit, paths)
        if spur is None:
            return None
        node_path = (spur + base[1:]) if on_return else (base + spur[1:])

    return path_to_candidate(graph, node_path), node_path


def _round_through_pair(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    amenity_node: int,
    target_distance_m: float,
    paths: dict[int, list[int]] | None = None,
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
        outbound = outbound_path(graph, start_node, amenity_node, paths)
    except RouteNotFoundError:
        return None

    # Try the roundest return first, stepping down to lighter penalties
    # (shorter returns) until the loop fits under target+tolerance, so the
    # residual can be spurred up to the exact length instead of the loop
    # overshooting with no way to shrink. Falls back to the shortest loop
    # found if none fit.
    ceiling = target_distance_m + DEFAULT_TOLERANCE_M
    best: tuple[RouteCandidate, list[int]] | None = None
    best_distance = float("inf")

    for penalty in _RETURN_PENALTIES:
        return_path = _return_path(
            graph, amenity_node, start_node, outbound, penalty
        )
        if return_path is None:
            continue

        loop = outbound + return_path[1:]
        candidate = path_to_candidate(graph, loop)

        if candidate.distance_m < best_distance:
            best = (candidate, loop)
            best_distance = candidate.distance_m

        if candidate.distance_m <= ceiling:
            # Roundest penalty that fits: spur the residual up to target.
            return tune_pair_to_target(
                graph, start_node, candidate, loop, target_distance_m,
                dists=dists, paths=paths,
            )

    if best is None:
        return None

    return tune_pair_to_target(
        graph, start_node, best[0], best[1], target_distance_m,
        dists=dists, paths=paths,
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
    dists: dict[int, float] | None = None,
    paths: dict[int, list[int]] | None = None,
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
    if dists is None:
        dists, paths = single_source_paths(graph, start_node)

    placements = _select_placements(
        snapped, dists, target_distance_m, min_range_m, max_range_m, shape, count
    )

    triples: list[tuple[RouteCandidate, list[int], Shape]] = []

    for placement in placements:
        amenity_node = placement.entry.node_id
        produced: list[tuple[RouteCandidate, list[int], Shape]] = []

        if shape in ("out_and_back", "mix"):
            oab = _out_and_back_placed(
                graph,
                start_node,
                dists,
                amenity_node,
                placement.d,
                target_distance_m,
                placement.on_return,
                paths,
            )
            if oab is not None:
                produced.append((oab[0], oab[1], "out_and_back"))

        # Round loops don't retrace, so only an outbound (front) placement
        # can be built as a genuine enclosing loop.
        if shape in ("round", "mix") and not placement.on_return:
            loop = _round_through_pair(
                graph, start_node, dists, amenity_node, target_distance_m, paths
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
