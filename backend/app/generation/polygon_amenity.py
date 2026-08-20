"""Amenity-aware polygon-loop round-route generator V2 (PR #16).

V1's amenity-first generator (`amenity_first.py`) treats the requested
amenity as the route's turnaround/far point:

    START ------------------ AMENITY
          \\__________________/

That reproduces exactly the elongation problem PR #15 fixed for the
ordinary (no-amenity) round case -- it also relies on
`length_tune.py`'s spur-based tuner, which can splice a start-return
out-and-back spur onto the route.

This module instead routes the amenity as a WAYPOINT on one leg of an
otherwise ordinary polygon loop (see `polygon_loop.py`):

    START -> B -> AMENITY -> C -> D -> START

The amenity constrains one section of a broad loop; it does not
dictate the loop's overall topology. Distance tuning rescales the
polygon and rebuilds (same strategy as `polygon_loop.py`), never
splicing a spur, so every candidate here has zero tuner-generated
start-return spurs by construction, same as the non-amenity V2 path.

This module shares its core loop-building/tuning machinery with
`polygon_loop.py` (`_NodeIndex`, `_build_loop_via_waypoints`,
`_tune_waypoints`, `_affine_calibration_scale_via`) rather than
duplicating it -- see that module's `Waypoint` type: a waypoint is
either a synthetic `Coordinate` needing graph-snapping, or an
already-known graph node id (`int`). The amenity's snapped node id is
spliced into the waypoint sequence at the position matching its
target leg. Distance calibration uses the AFFINE (two-probe) variant,
not the plain generator's single-probe one -- see
`_affine_calibration_scale_via`'s docstring for why a fixed waypoint
needs it.

IMPORTANT: this generator's own belief that it passed the amenity is
NOT authoritative. `app.generation.local_scoring.py` independently
re-derives the actual on-route amenity match (nearest-vertex distance
+ cumulative mile marker) from the candidate's rendered geometry, and
is the sole source of truth for the API's `matched` contract (restroom
only -- a fountain can never set `matched=True`, see
`local_scoring._partial_for`). This generator's own post-build
validation (`_try_amenity_placement`) exists only to avoid returning
obviously-wrong candidates (amenity not actually near the route, or
near it but at the wrong cumulative position) -- it reuses the exact
same `app.amenities.matching` helpers `local_scoring.py` uses, so the
two never disagree about what "on route" means.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.amenities.matching import AMENITY_PROXIMITY_THRESHOLD_M, match_amenities_to_route
from app.amenities.models import AmenityKind
from app.amenities.snapping import SnappedAmenity
from app.generation.polygon_loop import (
    DEFAULT_TOLERANCE_M,
    MAX_EDGE_REUSE_RATIO,
    Waypoint,
    _affine_calibration_scale_via,
    _NodeIndex,
    _tune_waypoints,
)
from app.generation.polygon_template import TEMPLATES, LoopTemplate, template_anchors
from app.generation.quality import edge_reuse_ratio
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import nearest_node, single_source_paths
from app.graph.model import node_coordinate
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RouteCandidate


LegName = Literal["AB", "BC", "CD", "DA"]

# A fixed amenity waypoint doesn't shrink with the rest of the polygon
# the way the plain generator's synthetic anchors do -- when its
# detour already consumes a large share of the target distance,
# hitting target requires shrinking the FREE anchors well below the
# plain generator's 0.4x floor (empirically, real-Manhattan cases
# needing this go as low as ~0.2x). Widen only the floor -- the
# ceiling (MAX_SCALE) is unaffected since overshoot behaves the same
# way regardless of the fixed waypoint.
AMENITY_MIN_SCALE = 0.1

# Even with the affine two-probe calibration (see
# `_affine_calibration_scale_via`), the affine model is only a local
# approximation -- the residual after the first build can still need a
# few proportional correction passes to close. Empirically, the plain
# generator's default (4) leaves some real-Manhattan amenity cases
# short; 6 clears most of them at a smaller latency cost than 8 (each
# extra pass only fires when the previous one missed tolerance, so the
# cost is paid only by scenarios that need it).
AMENITY_MAX_CORRECTION_ATTEMPTS = 6

# Restrooms are tried before fountains (product priority: a restroom
# can satisfy the API's hard `matched` contract, a fountain can only
# ever be a display fallback -- see module docstring). Both bounds are
# small and deterministic so the whole search stays a handful of real
# graph builds rather than "every amenity x every template": a round
# loop only has a few hundred candidate restrooms/fountains near any
# given start, and 4 restrooms + 2 fountains reliably finds a workable
# placement in practice (see scripts/benchmark_polygon_amenity.py).
RESTROOM_SHORTLIST_SIZE = 4
FOUNTAIN_SHORTLIST_SIZE = 2
# Overall shortlist budget -- fountains get whatever of this restrooms
# don't use (see `_shortlist_amenities`), so an amenity pool with few
# or zero restrooms still gets a full-sized fountain search instead of
# being capped at FOUNTAIN_SHORTLIST_SIZE.
TOTAL_SHORTLIST_SIZE = 6

# An amenity whose shortest-path distance from start approaches
# target_distance_m/2 requires a near out-and-back detour to reach at
# all on a COMPACT loop -- exactly the topology this generator exists
# to avoid (a small/collapsed "free" loop plus a long there-and-back
# spur to the amenity). PR #15's real-Manhattan top-ranked polygon
# loops have a p95 radial exposure (farthest point from start / route
# distance) of ~0.36 -- an amenity much farther than that from start
# isn't reachable by a well-formed compact loop at this target
# distance, whatever the tuner does. Intentionally tighter than
# amenity_first.py's out-and-back-oriented "target/2" reach bound,
# because a loop's natural reach from start is structurally smaller
# than an out-and-back's.
AMENITY_RADIAL_REACH_MARGIN = 0.42

# For each shortlisted amenity, try only its best-fit (template, leg)
# pair, then one runner-up if the first fails to build/validate --
# not all 10 TEMPLATES. Keeps worst-case cost bounded to
# ~(RESTROOM_SHORTLIST_SIZE + FOUNTAIN_SHORTLIST_SIZE) *
# AMENITY_TEMPLATE_ATTEMPTS real graph builds instead of exploding
# with the full template set.
AMENITY_TEMPLATE_ATTEMPTS = 2


def _leg_fractions(template: LoopTemplate) -> dict[LegName, tuple[float, float]]:
    """Cumulative [start_frac, end_frac) of each leg's share of the
    template's synthetic perimeter, walking A->B->C->D->A. `aspect_ratio`
    is height/width (see `polygon_template.template_anchors`), so AB/CD
    (the "height" legs) and BC/DA (the "width" legs) each get their
    fraction of the whole perimeter."""
    width_frac = 1.0 / (2.0 * (1.0 + template.aspect_ratio))
    height_frac = template.aspect_ratio * width_frac
    ab_end = height_frac
    bc_end = ab_end + width_frac
    cd_end = bc_end + height_frac
    return {
        "AB": (0.0, ab_end),
        "BC": (ab_end, bc_end),
        "CD": (bc_end, cd_end),
        "DA": (cd_end, 1.0),
    }


def _target_leg(template: LoopTemplate, target_fraction: float) -> LegName:
    """Which leg's synthetic perimeter span contains `target_fraction`
    -- a cheap first approximation of "the amenity should probably
    fall on this leg", refined below by `_leg_target_point` and
    finally validated for real against the built route's actual
    cumulative position in `_try_amenity_placement`."""
    fraction = min(max(target_fraction, 0.0), 0.999999)
    for leg, (start_frac, end_frac) in _leg_fractions(template).items():
        if start_frac <= fraction < end_frac:
            return leg
    return "DA"  # unreachable given the clamp above; satisfies mypy/defensive


def _leg_target_point(
    start_coord: Coordinate,
    anchors: tuple[Coordinate, Coordinate, Coordinate],
    template: LoopTemplate,
    leg: LegName,
    target_fraction: float,
) -> Coordinate:
    """Linearly-interpolated point along the synthetic leg segment at
    the requested cumulative fraction. This is a coarse target for
    RANKING nearby amenities/templates only -- never used as a routing
    constraint or a correctness check; the real graph path along this
    leg will not be a straight line."""
    b, c, d = anchors
    leg_points = {
        "AB": (start_coord, b),
        "BC": (b, c),
        "CD": (c, d),
        "DA": (d, start_coord),
    }
    p0, p1 = leg_points[leg]
    start_frac, end_frac = _leg_fractions(template)[leg]
    span = end_frac - start_frac
    local_fraction = 0.5 if span <= 0.0 else (target_fraction - start_frac) / span
    local_fraction = min(max(local_fraction, 0.0), 1.0)
    return Coordinate(
        lat=p0.lat + (p1.lat - p0.lat) * local_fraction,
        lon=p0.lon + (p1.lon - p0.lon) * local_fraction,
    )


def _best_templates_for_amenity(
    start_coord: Coordinate,
    target_distance_m: float,
    target_fraction: float,
    amenity_coord: Coordinate,
) -> list[tuple[LoopTemplate, LegName]]:
    """Every TEMPLATES entry paired with its target leg for this
    request, ranked by how close the template's interpolated target
    point (`_leg_target_point`) sits to the actual amenity -- cheap
    haversine-only ranking (all `template_anchors` calls here are pure
    trig, no graph/Dijkstra work), so trying only the top few is safe."""
    scored: list[tuple[float, LoopTemplate, LegName]] = []
    for template in TEMPLATES:
        b, c, d = template_anchors(start_coord, target_distance_m, template, 1.0)
        leg = _target_leg(template, target_fraction)
        target_point = _leg_target_point(start_coord, (b, c, d), template, leg, target_fraction)
        scored.append((haversine_m(amenity_coord, target_point), template, leg))

    scored.sort(key=lambda entry: entry[0])
    return [(template, leg) for _, template, leg in scored]


def _shortlist_amenities(
    snapped: list[SnappedAmenity],
    dists: dict[int, float],
    target_distance_m: float,
    min_range_m: float,
    max_range_m: float,
) -> list[SnappedAmenity]:
    """Cheap (no graph routing) pre-filter + rank, restrooms first.

    NOT a final acceptance test: `dists` is shortest graph distance
    from start, not cumulative along-route position -- exactly the
    assumption this PR replaces for candidate CONSTRUCTION. It is only
    used here to pick a small, geographically plausible set of
    amenities worth actually trying to route through; final acceptance
    is revalidated against the ACTUAL built route's geometry in
    `_try_amenity_placement`.
    """
    target_position_m = (min_range_m + max_range_m) / 2.0
    reach_ceiling_m = target_distance_m * AMENITY_RADIAL_REACH_MARGIN

    reachable = [
        entry
        for entry in snapped
        if (d := dists.get(entry.node_id)) is not None and 0.0 < d <= reach_ceiling_m
    ]

    def closeness(entry: SnappedAmenity) -> float:
        return abs(dists[entry.node_id] - target_position_m)

    restrooms = sorted(
        (e for e in reachable if e.amenity.kind == "restroom"), key=closeness
    )[:RESTROOM_SHORTLIST_SIZE]
    # Fountains fill whatever budget restrooms didn't use, not just a
    # fixed FOUNTAIN_SHORTLIST_SIZE -- the offline bundled dataset has
    # NO restrooms at all (restrooms are live-Supabase-only, see
    # module docstring), so a fixed small fountain cap would leave
    # most of the search budget unused whenever restrooms are sparse
    # or absent, rather than trying more fountain candidates instead.
    fountain_budget = max(
        FOUNTAIN_SHORTLIST_SIZE, TOTAL_SHORTLIST_SIZE - len(restrooms)
    )
    fountains = sorted(
        (e for e in reachable if e.amenity.kind == "fountain"), key=closeness
    )[:fountain_budget]

    return restrooms + fountains


def _amenity_waypoints_at_scale(
    start_coord: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    leg: LegName,
    amenity_node: int,
) -> Callable[[float], list[Waypoint]]:
    def waypoints_at_scale(scale: float) -> list[Waypoint]:
        b, c, d = template_anchors(start_coord, target_distance_m, template, scale)
        if leg == "AB":
            return [amenity_node, b, c, d]
        if leg == "BC":
            return [b, amenity_node, c, d]
        if leg == "CD":
            return [b, c, amenity_node, d]
        return [b, c, d, amenity_node]

    return waypoints_at_scale


@dataclass(frozen=True)
class _AmenityCandidate:
    candidate: RouteCandidate
    node_path: list[int]
    kind: AmenityKind
    isoperimetric_quotient: float


def _try_amenity_placement(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    start_coord: Coordinate,
    target_distance_m: float,
    entry: SnappedAmenity,
    template: LoopTemplate,
    leg: LegName,
    min_range_m: float,
    max_range_m: float,
    tolerance_m: float,
    paths: dict[int, list[int]] | None,
) -> _AmenityCandidate | None:
    """Build + tune one (amenity, template, leg) combination, then
    validate it two ways before accepting: (1) `edge_reuse_ratio`
    stays under the same `MAX_EDGE_REUSE_RATIO` ceiling as the
    non-amenity generator -- an amenity detour must not turn the loop
    into a near-retrace; (2) the amenity's ACTUAL along-route
    cumulative position (recomputed from the real rendered geometry,
    via the same `app.amenities.matching` helpers `local_scoring.py`
    uses) lands inside [min_range_m, max_range_m] -- the synthetic
    leg-fraction targeting above is only ever a search heuristic.
    """
    waypoints_at_scale = _amenity_waypoints_at_scale(
        start_coord, target_distance_m, template, leg, entry.node_id
    )
    calibration = _affine_calibration_scale_via(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m, paths,
        min_scale=AMENITY_MIN_SCALE,
    )
    result = _tune_waypoints(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m,
        calibration, tolerance_m, paths, min_scale=AMENITY_MIN_SCALE,
        max_correction_attempts=AMENITY_MAX_CORRECTION_ATTEMPTS,
        use_secant_refinement=True,
    )
    if result is None:
        return None

    candidate, node_path = result

    if edge_reuse_ratio(node_path) > MAX_EDGE_REUSE_RATIO:
        return None

    matches = match_amenities_to_route(
        candidate.geometry, [entry.amenity], max_distance_m=AMENITY_PROXIMITY_THRESHOLD_M
    )
    if not matches:
        return None  # amenity never actually comes near the built route

    mile_marker_m = matches[0].mile_marker_m
    if not (min_range_m <= mile_marker_m <= max_range_m):
        return None  # on the route, but at the wrong cumulative position

    return _AmenityCandidate(
        candidate=candidate,
        node_path=node_path,
        kind=entry.amenity.kind,
        isoperimetric_quotient=isoperimetric_quotient(candidate.geometry),
    )


def polygon_loop_through_amenities_pairs(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
    count: int,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
) -> list[tuple[RouteCandidate, list[int], Literal["round"]]]:
    """(candidate, node_path, "round") triples for amenity-passing
    polygon loops, ranked restrooms-first then roundest-first.

    Mirrors `amenity_first.through_amenities_pairs`'s return shape
    (triples `engine.generate_routes` can turn into `GeneratedRoute`s)
    but is round-only and built on the multi-anchor polygon loop
    instead of a turnaround-and-return. For each shortlisted amenity
    (`_shortlist_amenities`, restrooms prioritized), tries its best-fit
    (template, leg) pair(s) (`_best_templates_for_amenity`), builds and
    distance-tunes a loop with the amenity spliced in as a waypoint on
    that leg (`_try_amenity_placement`), and keeps only candidates that
    survive both the edge-reuse and actual-cumulative-position checks.
    Stops early once enough candidates are found. Returns fewer (or
    zero) candidates, never raises, when the local topology/amenity
    pool can't support a valid placement.
    """
    if target_distance_m <= 0 or not snapped:
        return []

    start_node = nearest_node(graph, start)
    dists, paths = single_source_paths(graph, start_node)
    node_index = _NodeIndex(graph)
    start_coord = node_coordinate(graph, start_node)

    target_position_m = (min_range_m + max_range_m) / 2.0
    target_fraction = target_position_m / target_distance_m

    shortlist = _shortlist_amenities(
        snapped, dists, target_distance_m, min_range_m, max_range_m
    )

    found: list[_AmenityCandidate] = []
    restroom_found = 0

    for entry in shortlist:
        amenity_coord = Coordinate(lat=entry.amenity.lat, lon=entry.amenity.lon)
        template_options = _best_templates_for_amenity(
            start_coord, target_distance_m, target_fraction, amenity_coord
        )[:AMENITY_TEMPLATE_ATTEMPTS]

        placed: _AmenityCandidate | None = None
        for template, leg in template_options:
            placed = _try_amenity_placement(
                graph, start_node, node_index, start_coord, target_distance_m,
                entry, template, leg, min_range_m, max_range_m, tolerance_m, paths,
            )
            if placed is not None:
                break

        if placed is not None:
            found.append(placed)
            if placed.kind == "restroom":
                restroom_found += 1

        # Restrooms satisfy the API's hard `matched` contract; fountains
        # are display-only fallback (see module docstring). Once there
        # are enough restroom-passing candidates to fill `count`, stop
        # immediately -- don't spend a real graph build on the
        # remaining (lower-priority) shortlist entries, fountains
        # included. This is the dominant cost in profiling (each
        # attempt is a handful of reuse-penalized Dijkstras plus
        # distance-tuning rebuilds), so skipping unneeded attempts is
        # the highest-leverage latency lever available.
        if restroom_found >= count:
            break
        if len(found) >= count:
            break

    restrooms = sorted(
        (c for c in found if c.kind == "restroom"),
        key=lambda c: c.isoperimetric_quotient,
        reverse=True,
    )
    fountains = sorted(
        (c for c in found if c.kind == "fountain"),
        key=lambda c: c.isoperimetric_quotient,
        reverse=True,
    )

    seen: set[tuple[tuple[float, float], ...]] = set()
    triples: list[tuple[RouteCandidate, list[int], Literal["round"]]] = []

    for c in restrooms + fountains:
        key = tuple((point.lat, point.lon) for point in c.candidate.geometry)
        if key in seen:
            continue
        seen.add(key)
        triples.append((c.candidate, c.node_path, "round"))
        if len(triples) >= count:
            break

    return triples


def generate_polygon_loop_through_amenities(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
    count: int,
) -> list[RouteCandidate]:
    """Thin wrapper around `polygon_loop_through_amenities_pairs` for
    callers that only need candidates (mirrors
    `amenity_first.generate_through_amenities`)."""
    triples = polygon_loop_through_amenities_pairs(
        graph, start, target_distance_m, snapped, min_range_m, max_range_m, count
    )
    return [candidate for candidate, _node_path, _shape in triples]
