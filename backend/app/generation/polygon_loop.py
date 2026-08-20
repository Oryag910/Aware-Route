"""Multi-anchor polygon-loop round-route generator V2 (PR #15).

V1's round-route generator (`round_route.py`) is structurally a single
turnaround: start -> outbound leg -> reuse-penalized return -> start.
PR #14 quantified that this produces elongated, corridor-like "loops"
that sit close to a perturbed out-and-back on every geometry metric.

This module replaces that structural assumption with a genuine
multi-anchor loop: start -> B -> C -> D -> start, where B/C/D are
synthetic rectangle corners (see `polygon_template.py`) snapped onto
the walk graph. The goal is a broad, closed footprint rather than one
turnaround and a detour home.

This is an experimental V2 generator offered ALONGSIDE V1, not a
replacement -- see `engine.generate_polygon_loop_candidates`. Nothing
in `engine.generate_candidates` / `generate_routes` /
`generate_amenity_aware` (V1's public entry points, including the
"mix" and amenity-aware pools) calls into this module; V1 remains the
only generator wired into production route generation until this V2
path is validated by benchmark (see scripts comparing V1 vs V2).
"""

from math import cos, radians
from typing import Any

import numpy as np

from app.generation.polygon_template import TEMPLATES, LoopTemplate, template_anchors
from app.generation.quality import edge_reuse_ratio
from app.generation.reuse_penalty import edge_pairs, reuse_penalized_path
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import nearest_node, outbound_path, single_source_paths
from app.graph.model import node_coordinate, path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate


class _NodeIndex:
    """Vectorized nearest-node lookup, built once per `polygon_loop_pairs`
    call and reused across every anchor snap.

    `app.graph.distances.nearest_node` (osmnx's `nearest_nodes`)
    rebuilds a full GeoDataFrame from the graph on every call -- fine
    for the single start-node lookup every other generator does, but
    this generator snaps 3 anchors x up to 10 templates x up to 3 scale
    attempts per request, and that rebuild dominated wall-clock time
    (measured ~3s of a ~4s call on the real Manhattan graph). This
    index builds numpy arrays of every node's lat/lon once and answers
    each anchor snap with a single vectorized nearest-neighbor search
    instead -- same graph, same node ids, no repeated GeoDataFrame
    construction. Distance is equirectangular (longitude scaled by
    cos(latitude), the same local-flat approximation
    `shape_metrics.isoperimetric_quotient` uses) -- accurate enough to
    pick the closest node; never used for anything measured or
    reported.
    """

    def __init__(self, graph: Any) -> None:
        self._node_ids = list(graph.nodes)
        self._lats = np.array([graph.nodes[n]["y"] for n in self._node_ids])
        self._lons = np.array([graph.nodes[n]["x"] for n in self._node_ids])

    def nearest(self, coord: Coordinate) -> int:
        lon_scale = cos(radians(coord.lat))
        dlat = self._lats - coord.lat
        dlon = (self._lons - coord.lon) * lon_scale
        index = int(np.argmin(dlat * dlat + dlon * dlon))
        return int(self._node_ids[index])


DEFAULT_TOLERANCE_M = 100.0

# Bounds on the per-template scale correction (see `_tune_template`).
# A synthetic rectangle's Euclidean perimeter is only an estimate of
# the actual routed distance -- corners rarely land on a street, and
# routing follows the grid rather than a straight line -- so these
# bounds are wider than length_tune.py's radius-scale bounds
# (0.6-1.1) to give the corrective search enough room to converge.
MIN_SCALE = 0.4
MAX_SCALE = 2.2
MAX_CORRECTION_ATTEMPTS = 4  # extra rebuilds beyond the initial calibrated attempt

# A genuine 4-leg loop should retrace almost nothing along the way.
# This is deliberately far below MAX_REASONABLE_REPEATED_SEGMENT_RATIO
# (0.15, scripts/benchmark_suite.py, tuned for V1's turnaround-shaped
# routes) -- a V2 candidate that needs this much retracing to close
# its loop has effectively degenerated into an out-and-back, so it is
# rejected outright rather than ranked low. The whole point of the
# multi-anchor topology is to avoid retracing, not merely tolerate it.
MAX_EDGE_REUSE_RATIO = 0.2


def _build_loop(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    template: LoopTemplate,
    start_coord: Coordinate,
    target_distance_m: float,
    scale: float,
    paths: dict[int, list[int]] | None,
) -> tuple[RouteCandidate, list[int]] | None:
    """Attempt one (template, scale) combination end to end: place
    synthetic anchors, snap each to the graph, and stitch reuse-
    penalized legs start -> B -> C -> D -> start.

    Guards against duplicate/collapsed anchors (any of B/C/D snapping
    onto the start node or onto an earlier anchor) and unreachable
    legs, returning None so the caller can cheaply move on to the next
    template/scale rather than raise.
    """
    b_coord, c_coord, d_coord = template_anchors(
        start_coord, target_distance_m, template, scale
    )

    b_node = node_index.nearest(b_coord)
    if b_node == start_node:
        return None
    c_node = node_index.nearest(c_coord)
    if c_node in (start_node, b_node):
        return None
    d_node = node_index.nearest(d_coord)
    if d_node in (start_node, b_node, c_node):
        return None

    try:
        leg_ab = outbound_path(graph, start_node, b_node, paths)
    except RouteNotFoundError:
        return None
    used = edge_pairs(leg_ab)

    leg_bc = reuse_penalized_path(graph, b_node, c_node, used)
    if leg_bc is None:
        return None
    used |= edge_pairs(leg_bc)

    leg_cd = reuse_penalized_path(graph, c_node, d_node, used)
    if leg_cd is None:
        return None
    used |= edge_pairs(leg_cd)

    leg_da = reuse_penalized_path(graph, d_node, start_node, used)
    if leg_da is None:
        return None

    full_path = leg_ab + leg_bc[1:] + leg_cd[1:] + leg_da[1:]
    if full_path[0] != start_node or full_path[-1] != start_node:
        return None  # defensive -- unreachable by construction

    return path_to_candidate(graph, full_path), full_path


def _tune_template(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    template: LoopTemplate,
    start_coord: Coordinate,
    target_distance_m: float,
    initial_scale: float,
    tolerance_m: float,
    paths: dict[int, list[int]] | None,
) -> tuple[RouteCandidate, list[int]] | None:
    """Bounded iterative distance tuning for one template.

    Builds the loop; if its distance misses target by more than
    tolerance, rescales the polygon by the observed target/actual
    ratio and rebuilds, up to `MAX_CORRECTION_ATTEMPTS` extra tries
    (bounded iterative correction, not a full binary search per
    template -- see module docstring / PR notes for why). Never
    splices an out-and-back spur the way V1's length_tune.py does: the
    closest attempt found is returned even if it still misses
    tolerance, so every V2 candidate has zero tuner-generated start
    spurs by construction.
    """
    scale = initial_scale
    best: tuple[RouteCandidate, list[int]] | None = None
    best_error = float("inf")

    for _ in range(1 + MAX_CORRECTION_ATTEMPTS):
        result = _build_loop(
            graph, start_node, node_index, template, start_coord, target_distance_m,
            scale, paths,
        )
        if result is None:
            break  # this template can't build at this scale -- don't force it

        candidate, _node_path = result
        error = abs(candidate.distance_m - target_distance_m)
        if error < best_error:
            best, best_error = result, error

        if best_error <= tolerance_m or candidate.distance_m <= 0:
            break

        scale = max(MIN_SCALE, min(MAX_SCALE, scale * (target_distance_m / candidate.distance_m)))

    return best


def _calibration_scale(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    start_coord: Coordinate,
    target_distance_m: float,
    paths: dict[int, list[int]] | None,
) -> float:
    """Probe-build the first template at scale=1.0 to estimate how much
    the graph inflates a synthetic rectangle's perimeter into an
    actual routed distance (streets rarely run in a straight line
    between corners, so this is consistently > 1 in practice). Reusing
    this estimate as every other template's starting scale means most
    templates need at most one correction pass instead of searching
    from scratch. Falls back to 1.0 (no calibration) if the probe
    template can't be built at all -- other templates then calibrate
    independently via their own `_tune_template` correction loop.
    """
    probe = _build_loop(
        graph, start_node, node_index, TEMPLATES[0], start_coord, target_distance_m,
        1.0, paths,
    )
    if probe is None or probe[0].distance_m <= 0:
        return 1.0
    return max(MIN_SCALE, min(MAX_SCALE, target_distance_m / probe[0].distance_m))


def polygon_loop_pairs(
    graph: Any,
    start_node: int,
    target_distance_m: float,
    count: int,
    paths: dict[int, list[int]] | None = None,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
) -> list[tuple[RouteCandidate, list[int]]]:
    """(candidate, node_path) pairs from the V2 multi-anchor polygon-loop
    generator, ranked roundest first.

    For each of the fixed `TEMPLATES` (varying rotation, aspect ratio,
    and cw/ccw traversal -- see `polygon_template.py`), synthesizes 3
    anchor coordinates around `start_node`, snaps them onto the graph,
    and stitches 4 reuse-penalized legs start -> B -> C -> D -> start
    (see `_build_loop`). Distance is tuned by rescaling the polygon and
    rebuilding (`_tune_template`), never by splicing a spur, so every
    returned candidate has its start-return-spur signal false by
    construction. Candidates that still retrace more than
    `MAX_EDGE_REUSE_RATIO` of their own edges are dropped, then
    survivors are ranked by isoperimetric quotient (roundest first)
    and the best `count` returned.

    Templates that cannot be snapped/routed at all (blocked bearing,
    disconnected anchor, target distance too small/large for the local
    street topology, ...) are silently skipped -- this generator
    degrades to fewer candidates (possibly zero) rather than raising
    when the local topology can't support a four-corner loop.
    """
    if target_distance_m <= 0:
        return []

    start_coord = node_coordinate(graph, start_node)
    node_index = _NodeIndex(graph)
    calibration = _calibration_scale(
        graph, start_node, node_index, start_coord, target_distance_m, paths
    )

    scored: list[tuple[float, RouteCandidate, list[int]]] = []

    for template in TEMPLATES:
        result = _tune_template(
            graph,
            start_node,
            node_index,
            template,
            start_coord,
            target_distance_m,
            calibration,
            tolerance_m,
            paths,
        )
        if result is None:
            continue

        candidate, node_path = result
        if edge_reuse_ratio(node_path) > MAX_EDGE_REUSE_RATIO:
            continue

        quotient = isoperimetric_quotient(candidate.geometry)
        scored.append((quotient, candidate, node_path))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [(candidate, node_path) for _, candidate, node_path in scored[:count]]


def generate_polygon_loop(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
) -> list[RouteCandidate]:
    """Public entry point mirroring `round_route.generate_round`, for
    the V2 polygon-loop generator. Computes its own start-node outbound
    paths so it stays fully independent of `engine.generate_candidates`
    and every V1 caller."""
    start_node = nearest_node(graph, start)
    _dists, paths = single_source_paths(graph, start_node)
    pairs = polygon_loop_pairs(graph, start_node, target_distance_m, count, paths)
    return [candidate for candidate, _node_path in pairs]
