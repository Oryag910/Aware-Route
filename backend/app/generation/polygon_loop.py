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

As of the round-generator migration (this revision), both explicit
`shape="round"` and `shape="mix"`'s round component go through ONE
shared seam (`engine._round_pairs`) that decides V1 vs polygon per
`ROUND_GENERATOR` -- previously "mix" silently hardcoded V1 regardless
of the flag, a gap this migration closes. `out_and_back` is untouched
regardless. Polygon is NOT the default yet, though: see
`engine._round_generator_version` for the full tradeoff this migration
measured -- geometry is substantially better and, at the product's
count=3 default, correctness/reliability matches or beats V1 (only
after `engine._round_pairs` added an in-tolerance-first ranking +
bounded V1 top-up, since this module's own "never splice a spur"
design can leave narrow/constrained local topology at large target
distances without enough in-tolerance candidates). Two gaps remain
open, though, even after a further round-trip that added a bounded
per-axis refinement for topology-constrained templates (see
`_refine_template_axes`/`axis_scaled_template_anchors`): the full-suite
p95 latency (~2.3s across repeated same-session runs, after a real
`reuse_penalty` optimization, this module's own scale-correction
plateau fix -- see `PLATEAU_DISTANCE_EPSILON_M` -- and the axis
refinement) still sits above the project's historical p95<2.0s
raw-generation gate, and is measurably WORSE than the prior round's
2.241s -- the axis refinement's own bounded extra work still lands on
the same extreme peninsula-tip/huge-target scenarios that already
dominate p95, since those rarely have enough natively-converging
templates to satisfy its early-exit gate. Separately, at the API's
supported count=5 (not the product default), round-shape reliability
is still meaningfully below V1 (~93.9% vs ~99.4% all-within-tolerance)
-- the in-tolerance-first fallback targets the product default, not
this wider case, and a diversity-selection fix
(`orchestration._select_diverse_within_tiers`) plus the axis refinement
each closed part, but not all, of this gap. `ROUND_GENERATOR=polygon`
opts in today; see
docs/benchmarks.md for the full same-commit V1-vs-polygon numbers
both gaps are based on.
"""

from collections.abc import Callable
from math import cos, radians
from typing import Any

import numpy as np

from app.generation.polygon_template import (
    TEMPLATES,
    LoopTemplate,
    axis_scaled_template_anchors,
    template_anchors,
)
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

# Distance tolerance for the plateau check in `_tune_waypoints`: a
# rescale attempt whose built distance is within this of the PREVIOUS
# attempt's is a NECESSARY but not sufficient signal that correction
# has plateaued -- see `_tune_waypoints`'s docstring for the measured
# mechanism (a synthetic anchor snapping to the same graph node
# regardless of how much further out it's requested, once the
# requested point is off-graph) and why the node path must ALSO match
# before concluding the route is genuinely unchanged (two different
# routes can coincidentally land on the same length on a grid-like
# street network). A true plateau reproduces the IDENTICAL route (same
# snapped nodes -> same shortest path -> same length), so this only
# needs to be larger than floating-point noise, not a real
# distance-tuning threshold like `DEFAULT_TOLERANCE_M`.
PLATEAU_DISTANCE_EPSILON_M = 1.0

# A genuine 4-leg loop should retrace almost nothing along the way. A
# plain out-and-back's edge_reuse_ratio is ~0.5 (every outbound edge
# gets revisited once on the return leg -- see
# quality.edge_reuse_ratio's docstring); 0.2 sits well below that
# baseline, so a V2 candidate crossing it has meaningfully degenerated
# toward an out-and-back rather than staying a broad loop, and is
# rejected outright rather than ranked low. This is a DIFFERENT metric
# from MAX_REASONABLE_REPEATED_SEGMENT_RATIO (0.15,
# scripts/benchmark_suite.py) -- that one measures rendered-geometry
# segment repeats on V1's turnaround-shaped routes, not node-path edge
# reuse -- so the two threshold values are not directly comparable
# despite looking similar.
MAX_EDGE_REUSE_RATIO = 0.2


#: A loop waypoint is either a synthetic coordinate that still needs
#: snapping to the graph (`Coordinate`) or an already-known graph node
#: id that must be used as-is (`int` -- e.g. an amenity's snapped
#: node). `polygon_amenity.py` inserts an `int` waypoint into the
#: sequence below to route THROUGH an amenity on one leg instead of
#: treating it as the loop's turnaround.
Waypoint = Coordinate | int


def _snap_waypoint(node_index: _NodeIndex, waypoint: Waypoint) -> int:
    return waypoint if isinstance(waypoint, int) else node_index.nearest(waypoint)


def _build_loop_via_waypoints(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    waypoints: list[Waypoint],
    paths: dict[int, list[int]] | None,
    should_continue: Callable[[], bool] | None = None,
) -> tuple[RouteCandidate, list[int]] | None:
    """Stitch start -> waypoints[0] -> waypoints[1] -> ... -> start.

    Shared core of both the plain 4-anchor polygon loop (`_build_loop`,
    waypoints = [B, C, D]) and the amenity-aware variant
    (`polygon_amenity.py`, which splices an already-snapped amenity
    node into the sequence). Each `Coordinate` waypoint is snapped to
    the graph; each `int` waypoint (already a node id) is used as-is.
    Guards against duplicate/collapsed waypoints (any waypoint
    resolving to the start node or an earlier waypoint's node) and
    unreachable legs, returning None so the caller can cheaply move on
    rather than raise. Every leg after the first accumulates used edges
    so later legs avoid retracing earlier ones (reuse-penalized
    routing), exactly like V1's turnaround-and-return but applied
    across every leg of the loop instead of just one.

    `should_continue` is an optional cooperative-cancellation callback
    (deliberately not a facilities-specific `PlanningDeadline` type --
    this module has no business knowing what a planning deadline is;
    callers with a time budget pass `lambda: not deadline.expired()`).
    Checked before each expensive graph-routing leg -- the only
    operations this function can't cheaply undo -- so a caller that
    stops asking for more work never gets back an INCOMPLETE route:
    any expiry here returns None, same as an unreachable leg. Plain,
    non-facility callers never pass this, so behavior is unchanged for
    them (the check is skipped entirely when `should_continue is
    None`).
    """
    nodes = [start_node]
    for waypoint in waypoints:
        node = _snap_waypoint(node_index, waypoint)
        if node in nodes:
            return None
        nodes.append(node)
    nodes.append(start_node)

    if should_continue is not None and not should_continue():
        return None

    try:
        first_leg = outbound_path(graph, start_node, nodes[1], paths)
    except RouteNotFoundError:
        return None
    full_path = list(first_leg)
    used = edge_pairs(first_leg)

    for i in range(1, len(nodes) - 1):
        if should_continue is not None and not should_continue():
            return None
        leg = reuse_penalized_path(graph, nodes[i], nodes[i + 1], used)
        if leg is None:
            return None
        full_path += leg[1:]
        used |= edge_pairs(leg)

    if full_path[0] != start_node or full_path[-1] != start_node:
        return None  # defensive -- unreachable by construction

    return path_to_candidate(graph, full_path), full_path


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
    synthetic anchors and stitch reuse-penalized legs
    start -> B -> C -> D -> start. Thin wrapper around
    `_build_loop_via_waypoints` for the plain (no-amenity) case."""
    b_coord, c_coord, d_coord = template_anchors(
        start_coord, target_distance_m, template, scale
    )
    return _build_loop_via_waypoints(
        graph, start_node, node_index, [b_coord, c_coord, d_coord], paths
    )


def _next_scale_estimate(
    history: list[tuple[float, float]],
    target_distance_m: float,
    last_scale: float,
    last_distance: float,
    min_scale: float,
    max_scale: float,
) -> float:
    """Secant-method estimate of the next scale to try, using the two
    most recent (scale, distance) points on file. Falls back to plain
    proportional correction (`scale *= target/actual`) when fewer than
    two points exist yet, or the two most recent points are
    degenerate (equal scale or equal distance -- no local slope to
    fit). See `_tune_waypoints`'s `use_secant_refinement` for when
    this is worth the extra bookkeeping over plain proportional
    correction."""
    if len(history) >= 2:
        (scale_a, distance_a), (scale_b, distance_b) = history[-2], history[-1]
        if scale_a != scale_b and distance_a != distance_b:
            slope = (distance_b - distance_a) / (scale_b - scale_a)
            if slope != 0:
                intercept = distance_a - slope * scale_a
                estimated = (target_distance_m - intercept) / slope
                return max(min_scale, min(max_scale, estimated))

    return max(min_scale, min(max_scale, last_scale * (target_distance_m / last_distance)))


def _tune_waypoints(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    waypoints_at_scale: Callable[[float], list[Waypoint]],
    target_distance_m: float,
    initial_scale: float,
    tolerance_m: float,
    paths: dict[int, list[int]] | None,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
    max_correction_attempts: int = MAX_CORRECTION_ATTEMPTS,
    use_secant_refinement: bool = False,
    should_continue: Callable[[], bool] | None = None,
) -> tuple[RouteCandidate, list[int]] | None:
    """Bounded iterative distance tuning shared by the plain and
    amenity-aware generators.

    Builds the loop from `waypoints_at_scale(scale)`; if its distance
    misses target by more than tolerance, picks a new scale and
    rebuilds, up to `max_correction_attempts` extra tries (bounded
    iterative correction, not a full binary search -- see module
    docstring for why). Never splices an out-and-back spur the way
    V1's length_tune.py does: the closest attempt found is returned
    even if it still misses tolerance, so every V2 candidate has zero
    tuner-generated start spurs by construction.

    `min_scale`/`max_scale` default to the plain generator's bounds
    but are overridable: `polygon_amenity.py` widens the floor,
    because a FIXED waypoint (the amenity) doesn't shrink with the
    rest of the polygon -- when its detour already consumes a large
    share of the target distance, hitting target requires shrinking
    the free anchors well below 0.4x, which the plain generator never
    needs to do (its whole loop scales together).

    `use_secant_refinement` (default False, preserving the plain
    generator's exact original behavior) switches the correction step
    from plain proportional (`scale *= target/actual`) to a secant fit
    over the two most recent tried points once at least two exist (see
    `_next_scale_estimate`). Proportional correction converges slowly
    -- sometimes taking its whole attempt budget in tiny single-digit-
    percent steps without closing the gap -- when a large FIXED
    component (an amenity's detour) makes distance a poor proportional
    function of scale; a secant fit re-estimates the true local slope
    from real builds every step, closing in far faster. Only
    `polygon_amenity.py` opts in; the plain (no-amenity) path has no
    fixed component, so proportional correction already works well
    there and this stays off to avoid changing its validated behavior.

    `should_continue` (see `_build_loop_via_waypoints`) is checked
    before every rebuild attempt, including the first -- an expired
    budget means no correction attempt starts, and `best` (whatever was
    already built, `None` if nothing was) is returned as-is rather than
    attempting one more rebuild.

    Correction also stops early if a rebuild PLATEAUS (see
    `PLATEAU_DISTANCE_EPSILON_M`) -- measured root cause: when a
    template's rotation points toward the edge of the routable graph
    (a peninsula tip's water boundary, a park/highway edge with no
    further street network), the synthetic anchor keeps moving further
    away as `scale` grows, but `_NodeIndex.nearest()` keeps snapping it
    to the SAME boundary-closest graph node -- so the built route (same
    snapped nodes -> same shortest path) is bit-for-bit identical no
    matter how many more scale corrections are tried. Continuing to
    rebuild in that state is pure wasted Dijkstra work (confirmed:
    scale 1.5/2.0/2.2/3.0/5.0 all snapped to the same node in one
    measured hard case) chasing a target this orientation cannot reach
    at any scale; `best` already holds whatever this template's closest
    honest attempt was, unaffected by stopping early.

    A plateau requires BOTH the built distance AND the built node path
    to match the previous attempt -- distance alone is not sufficient
    proof: on a grid-like street network, two genuinely DIFFERENT node
    paths (different snapped anchors, different routed streets) can
    legitimately land on equal or near-equal total length by
    coincidence, and that candidate may still be actively converging
    toward target on a later attempt. Only an identical node path
    proves the route itself is unchanged -- i.e. that a further
    rescale is provably wasted rather than just currently unlucky.
    """
    scale = initial_scale
    best: tuple[RouteCandidate, list[int]] | None = None
    best_error = float("inf")
    history: list[tuple[float, float]] = []
    last_distance: float | None = None
    last_node_path: list[int] | None = None

    for _ in range(1 + max_correction_attempts):
        if should_continue is not None and not should_continue():
            break
        result = _build_loop_via_waypoints(
            graph, start_node, node_index, waypoints_at_scale(scale), paths, should_continue,
        )
        if result is None:
            break  # can't build at this scale -- don't force it

        candidate, node_path = result
        distance = candidate.distance_m
        error = abs(distance - target_distance_m)
        if error < best_error:
            best, best_error = result, error

        if best_error <= tolerance_m or distance <= 0:
            break

        same_distance = (
            last_distance is not None and abs(distance - last_distance) <= PLATEAU_DISTANCE_EPSILON_M
        )
        same_path = last_node_path is not None and node_path == last_node_path
        if same_distance and same_path:
            break  # plateaued -- further rescaling won't change the built route
        last_distance = distance
        last_node_path = node_path

        if use_secant_refinement:
            history.append((scale, distance))
            scale = _next_scale_estimate(
                history, target_distance_m, scale, distance, min_scale, max_scale
            )
        else:
            scale = max(min_scale, min(max_scale, scale * (target_distance_m / distance)))

    return best


# Bounded, fixed ratio pairs tried by `_tune_template`'s per-axis
# refinement below when a template's uniform-scale search still misses
# tolerance. Measured, not guessed: grid-testing a range of (height,
# width) multiplier pairs against known near-miss templates (hard
# scenarios where 4/5 or 3/5 templates converge -- see PR history)
# found real, decisive improvements concentrated at exactly these two
# ratios -- e.g. one Hamilton Heights template's error dropped from
# 272m to 44m (crossing into tolerance) at (0.6, 1.5), and a different
# Lower East Side template similarly crossed into tolerance at the
# same ratio, while an Inwood template instead improved at the inverse
# (1.5, 0.6). Intermediate ratios (0.7/1.3) found no improvement at
# all in the same sweep. Only these two are tried -- not a search --
# to keep the refinement bounded and cheap; which direction (if
# either) helps depends on which axis a template's local topology
# constrains, which isn't reliably predictable from snap error alone
# (also measured), so both are tried and the better result kept.
AXIS_REFINEMENT_RATIOS: tuple[tuple[float, float], ...] = ((0.6, 1.5), (1.5, 0.6))

# Only attempt the per-axis refinement (see `_refine_template_axes`)
# when a template's uniform-scale error is within this of target.
# Measured: every real improvement found in the ratio sweep this
# constant is based on started from a baseline error at or below
# ~300m (one axis moderately constrained, not the whole template);
# templates with errors of several kilometers (e.g. a peninsula-tip
# orientation where ALL anchors -- not just one axis -- snap far from
# their synthetic targets) never improved under either ratio, just
# paying for two wasted graph builds.
MAX_AXIS_REFINEMENT_ERROR_M = 300.0


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
    """Bounded iterative distance tuning for one plain (no-amenity)
    template. Thin wrapper around `_tune_waypoints`."""

    def waypoints_at_scale(scale: float) -> list[Waypoint]:
        b, c, d = template_anchors(start_coord, target_distance_m, template, scale)
        return [b, c, d]

    return _tune_waypoints(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m,
        initial_scale, tolerance_m, paths,
    )


def _refine_template_axes(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    template: LoopTemplate,
    start_coord: Coordinate,
    target_distance_m: float,
    initial_scale: float,
    tolerance_m: float,
    paths: dict[int, list[int]] | None,
    best: tuple[RouteCandidate, list[int]],
    best_error: float,
) -> tuple[tuple[RouteCandidate, list[int]], float]:
    """Bounded per-axis refinement for ONE template whose uniform-scale
    result (`best`/`best_error`) still misses `tolerance_m`.

    A single shared `scale` grows/shrinks the "height" legs (A->B,
    C->D) and the "width" leg (B->C) together. When one axis points
    toward locally constrained topology (e.g. a peninsula's water
    boundary) while the other still has room, that shared scale forces
    both to the same compromise and can't reach target no matter how
    many correction attempts run (measured: `max_correction_attempts`
    up to 20 left several real near-miss templates completely
    unmoved). This tries the template's two fixed asymmetric (height,
    width) ratios (`AXIS_REFINEMENT_RATIOS`) via
    `axis_scaled_template_anchors` instead, redistributing the
    distance budget toward whichever axis actually has room. Each
    ratio costs exactly one additional build (no further correction
    search), so this is bounded at 2 extra graph builds -- and is only
    ever called by `polygon_loop_pairs`'s caller once per template, and
    only when the scenario doesn't already have enough in-tolerance
    candidates from the uniform-scale pass (see `polygon_loop_pairs`),
    so it costs nothing on the common case where a request's ordinary
    templates already supply enough good candidates."""
    for height_ratio, width_ratio in AXIS_REFINEMENT_RATIOS:
        waypoints = axis_scaled_template_anchors(
            start_coord, target_distance_m, template,
            initial_scale * height_ratio, initial_scale * width_ratio,
        )
        axis_result = _build_loop_via_waypoints(
            graph, start_node, node_index, list(waypoints), paths,
        )
        if axis_result is None:
            continue
        axis_candidate, axis_node_path = axis_result
        if edge_reuse_ratio(axis_node_path) > MAX_EDGE_REUSE_RATIO:
            continue  # don't trade Polygon's geometry guarantee for distance
        error = abs(axis_candidate.distance_m - target_distance_m)
        if error < best_error:
            best, best_error = axis_result, error
            if best_error <= tolerance_m:
                break

    return best, best_error


def _calibration_scale_via(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    waypoints_at_scale: Callable[[float], list[Waypoint]],
    target_distance_m: float,
    paths: dict[int, list[int]] | None,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
    should_continue: Callable[[], bool] | None = None,
) -> float:
    """Probe-build `waypoints_at_scale(1.0)` to estimate how much the
    graph inflates a synthetic perimeter into an actual routed
    distance (streets rarely run in a straight line between corners,
    so this is consistently > 1 in practice). Reusing this estimate as
    a starting scale means most callers need at most one correction
    pass instead of searching from scratch. Falls back to 1.0 (no
    calibration) if the probe waypoints can't be built at all -- the
    caller's own `_tune_waypoints` correction loop then calibrates
    independently. `min_scale`/`max_scale` mirror `_tune_waypoints`'s
    overridable bounds.

    `should_continue` (see `_build_loop_via_waypoints`) is checked
    before the probe build -- an expired budget returns the neutral
    1.0 fallback without starting any graph work at all."""
    if should_continue is not None and not should_continue():
        return 1.0
    probe = _build_loop_via_waypoints(
        graph, start_node, node_index, waypoints_at_scale(1.0), paths, should_continue,
    )
    if probe is None or probe[0].distance_m <= 0:
        return 1.0
    return max(min_scale, min(max_scale, target_distance_m / probe[0].distance_m))


def _affine_calibration_scale_via(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    waypoints_at_scale: Callable[[float], list[Waypoint]],
    target_distance_m: float,
    paths: dict[int, list[int]] | None,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
    probe_scales: tuple[float, float] = (0.3, 1.0),
    should_continue: Callable[[], bool] | None = None,
) -> float:
    """Two-probe affine estimate of the scale that should hit target.

    `_calibration_scale_via`'s single-probe estimate assumes distance
    is roughly PROPORTIONAL to scale (true for the plain generator,
    where the whole loop scales together). That assumption breaks when
    `waypoints_at_scale` includes a FIXED, non-scaling waypoint (e.g.
    `polygon_amenity.py`'s amenity node): its detour doesn't shrink
    with the rest of the polygon, so distance is closer to an AFFINE
    function of scale (`distance ~= intercept + slope * scale`) with a
    large fixed intercept -- and `_tune_waypoints`'s proportional
    per-step correction (`scale *= target/actual`) converges very
    slowly against a large intercept (each step's ratio stays close to
    1 even far from the right scale).

    This builds two real probes and solves the affine model directly
    for the scale that should hit target, which converges in one step
    where the proportional approach could take many. Falls back to
    `_calibration_scale_via`'s single-probe estimate if either probe
    fails to build or the fit is degenerate (non-positive slope, or
    equal probe scales).

    `should_continue` (see `_build_loop_via_waypoints`) is checked
    before probe A, again before probe B, and again before falling
    back to `_calibration_scale_via` -- an expired budget at any of
    these points returns the neutral 1.0 fallback immediately rather
    than starting another probe build.
    """
    if should_continue is not None and not should_continue():
        return 1.0

    scale_a, scale_b = probe_scales
    probe_a = _build_loop_via_waypoints(
        graph, start_node, node_index, waypoints_at_scale(scale_a), paths, should_continue,
    )

    if should_continue is not None and not should_continue():
        return 1.0

    probe_b = _build_loop_via_waypoints(
        graph, start_node, node_index, waypoints_at_scale(scale_b), paths, should_continue,
    )
    if probe_a is None or probe_b is None or scale_a == scale_b:
        if should_continue is not None and not should_continue():
            return 1.0
        return _calibration_scale_via(
            graph, start_node, node_index, waypoints_at_scale, target_distance_m,
            paths, min_scale, max_scale, should_continue,
        )

    distance_a, distance_b = probe_a[0].distance_m, probe_b[0].distance_m
    slope = (distance_b - distance_a) / (scale_b - scale_a)
    if slope <= 0:
        return _calibration_scale_via(
            graph, start_node, node_index, waypoints_at_scale, target_distance_m,
            paths, min_scale, max_scale,
        )

    intercept = distance_a - slope * scale_a
    estimated_scale = (target_distance_m - intercept) / slope
    return max(min_scale, min(max_scale, estimated_scale))


def _calibration_scale(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    start_coord: Coordinate,
    target_distance_m: float,
    paths: dict[int, list[int]] | None,
) -> float:
    """Probe the first plain template at scale=1.0. Thin wrapper
    around `_calibration_scale_via`."""

    def waypoints_at_scale(scale: float) -> list[Waypoint]:
        b, c, d = template_anchors(start_coord, target_distance_m, TEMPLATES[0], scale)
        return [b, c, d]

    return _calibration_scale_via(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m, paths
    )


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

    A second, GATED pass follows the ordinary uniform-scale pass above:
    if fewer than `count` templates already landed within `tolerance_m`
    (only then -- see `_refine_template_axes`), the near-miss templates
    (in ranked, roundest-first order) get a bounded per-axis
    refinement attempt, stopping the moment enough in-tolerance
    candidates exist. This is an "early exit once enough good
    candidates exist" pattern: the common case, where the uniform-scale
    pass alone already supplies `count` or more in-tolerance
    candidates, pays nothing extra at all.
    """
    if target_distance_m <= 0:
        return []

    start_coord = node_coordinate(graph, start_node)
    node_index = _NodeIndex(graph)
    calibration = _calibration_scale(
        graph, start_node, node_index, start_coord, target_distance_m, paths
    )

    built: list[tuple[LoopTemplate, RouteCandidate, list[int], float]] = []

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

        error = abs(candidate.distance_m - target_distance_m)
        built.append((template, candidate, node_path, error))

    within_tolerance = sum(1 for *_rest, error in built if error <= tolerance_m)

    if within_tolerance < count:
        # Try the closest-first near-miss templates for axis refinement,
        # stopping as soon as this scenario has enough in-tolerance
        # candidates -- never refines every miss unconditionally.
        built.sort(key=lambda entry: entry[3])
        refined: list[tuple[LoopTemplate, RouteCandidate, list[int], float]] = []
        for template, candidate, node_path, error in built:
            if (
                within_tolerance < count
                and tolerance_m < error <= MAX_AXIS_REFINEMENT_ERROR_M
            ):
                (candidate, node_path), new_error = _refine_template_axes(
                    graph, start_node, node_index, template, start_coord,
                    target_distance_m, calibration, tolerance_m, paths,
                    (candidate, node_path), error,
                )
                if new_error <= tolerance_m and error > tolerance_m:
                    within_tolerance += 1
                error = new_error
            refined.append((template, candidate, node_path, error))
        built = refined

    scored = [
        (isoperimetric_quotient(candidate.geometry), candidate, node_path)
        for _template, candidate, node_path, _error in built
    ]
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
