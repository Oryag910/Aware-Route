"""Multi-facility Polygon round-route planner (Phase 3, PR #18).

Generalizes `app/generation/polygon_amenity.py`'s single-amenity
technique -- splice one amenity onto ONE leg of a multi-anchor polygon
loop -- to a variable-length, typed list of facility requirements that
can span several legs of the same loop:

    START -> [facilities on AB] -> B -> [facilities on BC] -> C
           -> [facilities on CD] -> D -> [facilities on DA] -> START

Built entirely on `app/generation/polygon_loop.py`'s shared machinery
(`_build_loop_via_waypoints`, `_NodeIndex`, `_tune_waypoints`,
`_affine_calibration_scale_via`) -- no separate graph stitcher, and
`length_tune.py`'s spur tuner is never used, so every candidate here has
zero tuner-generated start-return spurs by construction, same guarantee
`polygon_loop.py`/`polygon_amenity.py` already give.

This module does NOT itself decide which requirement is "satisfied" --
`app/facilities/orchestration.py`'s `plan_routes` re-scores every
candidate (natural and constrained) against the FINISHED route geometry
using the authoritative generic matcher/assignment engine
(`app/facilities/encounters.py` + `app/facilities/assignment.py`). This
module's job is only to PROPOSE a bounded number of spatially plausible
candidates likely to pass the requested facilities near the requested
cumulative distances -- cheap heuristic ranking, not correctness.

Search is bounded independent of facilities^requirements: per-requirement
shortlists and a beam search rank candidate PLANS cheaply (pure
haversine geometry, no graph routing); only a small global budget of the
best-ranked plans ever pay for a real graph build + distance tuning.
"""

from dataclasses import dataclass
from typing import Any, Literal

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
from app.generation.routes import GeneratedRoute, Shape as RouteShape, compute_quality
from app.graph.distances import nearest_node, single_source_paths
from app.graph.model import node_coordinate
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RouteCandidate

from app.facilities.models import Facility, FacilityRequirement, SnappedFacility
from app.facilities.planning_deadline import PlanningDeadline
from app.facilities.snapping import snap_facilities


Shape = Literal["round", "out_and_back", "mix"]
LegName = Literal["AB", "BC", "CD", "DA"]

_LEG_ORDER: tuple[LegName, ...] = ("AB", "BC", "CD", "DA")
_LEG_RANK: dict[LegName, int] = {leg: index for index, leg in enumerate(_LEG_ORDER)}

# Bounded-search constants. Not yet empirically tuned against the real
# Manhattan graph's latency budget (see benchmarks in Phase 7) -- chosen
# to mirror polygon_amenity.py's proven single-amenity bounds, scaled
# modestly for the multi-requirement case. All four multiply together
# only in the CHEAP ranking stage (pure haversine distance, no graph
# routing); the expensive real-graph-build stage is capped independently
# by FULL_BUILD_BUDGET regardless of how many templates/plans were
# ranked.
SHORTLIST_SIZE = 4
BEAM_WIDTH = 6
PLANS_PER_TEMPLATE = 2
FULL_BUILD_BUDGET = 6

# A fixed facility waypoint doesn't shrink with the rest of the polygon
# the way its synthetic anchors do; multiple fixed waypoints can consume
# an even larger share of the target distance than polygon_amenity.py's
# single-amenity case, so the scale floor is widened further than that
# module's AMENITY_MIN_SCALE=0.1.
ROUND_PLANNER_MIN_SCALE = 0.05
ROUND_PLANNER_MAX_CORRECTION_ATTEMPTS = 6


def _adaptive_build_budget(requirement_count: int) -> int:
    """How many of `FULL_BUILD_BUDGET`'s expensive real-graph builds this
    call gets, based on requirement count.

    The Aug 25 facility stress benchmark (docs/benchmarks.md) shows a
    single genuinely-hard requirement is reliably solved at the full
    budget (18/18 fully valid), but 2+ simultaneous hard requirements
    already succeed in only 0-2.8% of stress cases even when the planner
    runs to its full budget -- the extra builds buy latency there, not a
    meaningfully better success rate. Preserve the proven single-
    requirement budget in full and shrink it for the harder, already-
    unreliable cases so worst-case latency doesn't scale with a search
    that has sharply diminishing returns.
    """
    if requirement_count <= 1:
        return FULL_BUILD_BUDGET
    if requirement_count == 2:
        return 4
    return 3


def _leg_fractions(template: LoopTemplate) -> dict[LegName, tuple[float, float]]:
    width_frac = 1.0 / (2.0 * (1.0 + template.aspect_ratio))
    height_frac = template.aspect_ratio * width_frac
    ab_end = height_frac
    bc_end = ab_end + width_frac
    cd_end = bc_end + height_frac
    return {"AB": (0.0, ab_end), "BC": (ab_end, bc_end), "CD": (bc_end, cd_end), "DA": (cd_end, 1.0)}


def _target_leg(fractions: dict[LegName, tuple[float, float]], target_fraction: float) -> LegName:
    fraction = min(max(target_fraction, 0.0), 0.999999)
    for leg in _LEG_ORDER:
        start_frac, end_frac = fractions[leg]
        if start_frac <= fraction < end_frac:
            return leg
    return "DA"


def _leg_target_point(
    start_coord: Coordinate,
    anchors: tuple[Coordinate, Coordinate, Coordinate],
    fractions: dict[LegName, tuple[float, float]],
    leg: LegName,
    target_fraction: float,
) -> Coordinate:
    b, c, d = anchors
    leg_points: dict[LegName, tuple[Coordinate, Coordinate]] = {
        "AB": (start_coord, b), "BC": (b, c), "CD": (c, d), "DA": (d, start_coord),
    }
    p0, p1 = leg_points[leg]
    start_frac, end_frac = fractions[leg]
    span = end_frac - start_frac
    local_fraction = 0.5 if span <= 0.0 else (target_fraction - start_frac) / span
    local_fraction = min(max(local_fraction, 0.0), 1.0)
    return Coordinate(
        lat=p0.lat + (p1.lat - p0.lat) * local_fraction,
        lon=p0.lon + (p1.lon - p0.lon) * local_fraction,
    )


@dataclass(frozen=True)
class _PlacedRequirement:
    requirement: FacilityRequirement
    leg: LegName
    target_fraction: float
    target_point: Coordinate


def _requirement_legs(
    start_coord: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    requirements: list[FacilityRequirement],
) -> list[_PlacedRequirement]:
    """Which leg each requirement's midpoint falls on for this template,
    at scale=1.0 -- a cheap ranking heuristic only (see module docstring),
    never a routing constraint."""
    b, c, d = template_anchors(start_coord, target_distance_m, template, 1.0)
    fractions = _leg_fractions(template)

    placed = []
    for requirement in requirements:
        midpoint = (requirement.min_distance_m + requirement.max_distance_m) / 2.0
        target_fraction = midpoint / target_distance_m
        leg = _target_leg(fractions, target_fraction)
        point = _leg_target_point(start_coord, (b, c, d), fractions, leg, target_fraction)
        placed.append(_PlacedRequirement(requirement, leg, target_fraction, point))

    placed.sort(key=lambda p: (_LEG_RANK[p.leg], p.target_fraction, p.requirement.id))
    return placed


def _shortlist(
    snapped_by_kind: dict[str, list[SnappedFacility]],
    placed: _PlacedRequirement,
    limit: int,
) -> list[SnappedFacility]:
    candidates = snapped_by_kind.get(placed.requirement.kind, [])
    ranked = sorted(
        candidates,
        key=lambda s: haversine_m(
            Coordinate(lat=s.facility.lat, lon=s.facility.lon), placed.target_point
        ),
    )
    return ranked[:limit]


@dataclass(frozen=True)
class _Plan:
    cost: float
    # Parallel to the (filtered) `ordered` requirement list: one chosen
    # graph node id per placeable requirement, in the same order.
    node_ids: tuple[int, ...]


def _beam_search(
    ordered: list[_PlacedRequirement],
    shortlists: list[list[SnappedFacility]],
) -> list[_Plan]:
    """Cheap (no graph routing) beam search over per-requirement
    shortlist choices, one requirement at a time, keeping the
    `BEAM_WIDTH` lowest-cost partial plans. Requirements with an empty
    shortlist (no compatible-kind facility snapped at all) are already
    excluded from `ordered`/`shortlists` by the caller."""
    beams: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]

    for placed, shortlist in zip(ordered, shortlists):
        target = placed.target_point
        expanded: list[tuple[float, tuple[int, ...]]] = []
        for cost, node_ids in beams:
            for facility in shortlist:
                if facility.node_id in node_ids:
                    continue
                distance = haversine_m(
                    Coordinate(lat=facility.facility.lat, lon=facility.facility.lon), target
                )
                expanded.append((cost + distance, node_ids + (facility.node_id,)))
        if not expanded:
            # No compatible choice extends any partial plan -- every
            # plan in this beam dies for this template; caller treats an
            # empty result as "no viable plan from this template".
            return []
        expanded.sort(key=lambda entry: entry[0])
        beams = expanded[:BEAM_WIDTH]

    return [_Plan(cost=cost, node_ids=node_ids) for cost, node_ids in beams]


def _waypoints_at_scale(
    start_coord: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    ordered: list[_PlacedRequirement],
    node_ids: tuple[int, ...],
) -> "Any":
    def build(scale: float) -> list[Waypoint]:
        b, c, d = template_anchors(start_coord, target_distance_m, template, scale)
        anchor_after: dict[LegName, Coordinate] = {"AB": b, "BC": c, "CD": d}

        waypoints: list[Waypoint] = []
        for leg in _LEG_ORDER:
            for placed, node_id in zip(ordered, node_ids):
                if placed.leg == leg:
                    waypoints.append(node_id)
            if leg in anchor_after:
                waypoints.append(anchor_after[leg])
        return waypoints

    return build


def _build_plan(
    graph: Any,
    start_node: int,
    node_index: _NodeIndex,
    start_coord: Coordinate,
    target_distance_m: float,
    template: LoopTemplate,
    ordered: list[_PlacedRequirement],
    plan: _Plan,
    paths: dict[int, list[int]] | None,
) -> tuple[RouteCandidate, list[int]] | None:
    waypoints_at_scale = _waypoints_at_scale(
        start_coord, target_distance_m, template, ordered, plan.node_ids
    )
    calibration = _affine_calibration_scale_via(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m, paths,
        min_scale=ROUND_PLANNER_MIN_SCALE,
    )
    result = _tune_waypoints(
        graph, start_node, node_index, waypoints_at_scale, target_distance_m,
        calibration, DEFAULT_TOLERANCE_M, paths,
        min_scale=ROUND_PLANNER_MIN_SCALE,
        max_correction_attempts=ROUND_PLANNER_MAX_CORRECTION_ATTEMPTS,
        use_secant_refinement=True,
    )
    if result is None:
        return None

    candidate, node_path = result
    if edge_reuse_ratio(node_path) > MAX_EDGE_REUSE_RATIO:
        return None

    return candidate, node_path


def plan_constrained_round(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
    requirements: list[FacilityRequirement],
    facilities: list[Facility],
    deadline: PlanningDeadline | None = None,
) -> list[GeneratedRoute]:
    """Bounded multi-facility Polygon round planner. Returns `[]`
    immediately for shapes that could never use a round candidate, or
    when there's nothing to place -- this planner's search machinery
    must never run for an ordinary no-facility/out-and-back-only
    request.

    `deadline` is a cooperative, shared-across-planners wall-clock
    budget (see `app/facilities/planning_deadline.py`) checked before
    each expensive real graph build; if unset, a fresh default-budget
    deadline is used so this planner remains safely callable on its own
    (e.g. from tests)."""
    if shape not in ("round", "mix") or not requirements or target_distance_m <= 0:
        return []

    if deadline is None:
        deadline = PlanningDeadline()

    snapped = snap_facilities(graph, facilities)
    if not snapped:
        return []

    snapped_by_kind: dict[str, list[SnappedFacility]] = {}
    for entry in snapped:
        snapped_by_kind.setdefault(entry.facility.kind, []).append(entry)

    start_node = nearest_node(graph, start)
    _dists, paths = single_source_paths(graph, start_node)
    node_index = _NodeIndex(graph)
    start_coord = node_coordinate(graph, start_node)

    # Cheap stage: rank every template's best beam plan by pure haversine
    # cost, across ALL templates (no graph routing yet).
    ranked_plans: list[tuple[float, LoopTemplate, list[_PlacedRequirement], _Plan]] = []

    for template in TEMPLATES:
        placed_all = _requirement_legs(start_coord, target_distance_m, template, requirements)

        ordered: list[_PlacedRequirement] = []
        shortlists: list[list[SnappedFacility]] = []
        for placed in placed_all:
            shortlist = _shortlist(snapped_by_kind, placed, SHORTLIST_SIZE)
            if not shortlist:
                continue  # nothing of this kind exists to place -- skip, don't force
            ordered.append(placed)
            shortlists.append(shortlist)

        if not ordered:
            continue

        plans = _beam_search(ordered, shortlists)
        for plan in plans[:PLANS_PER_TEMPLATE]:
            ranked_plans.append((plan.cost, template, ordered, plan))

    ranked_plans.sort(key=lambda entry: entry[0])

    routes: list[GeneratedRoute] = []
    seen_geometry: set[tuple[tuple[float, float], ...]] = set()
    builds = 0
    build_budget = _adaptive_build_budget(len(requirements))

    for _cost, template, ordered, plan in ranked_plans:
        if builds >= build_budget or deadline.expired():
            break
        builds += 1

        built = _build_plan(
            graph, start_node, node_index, start_coord, target_distance_m,
            template, ordered, plan, paths,
        )
        if built is None:
            continue

        candidate, node_path = built
        key = tuple((point.lat, point.lon) for point in candidate.geometry)
        if key in seen_geometry:
            continue
        seen_geometry.add(key)

        route_shape: RouteShape = "round"
        routes.append(
            GeneratedRoute(
                candidate=candidate,
                node_path=node_path,
                shape=route_shape,
                quality=compute_quality(graph, node_path, candidate, route_shape),
            )
        )

        if len(routes) >= max(count * 2, count):
            break

    return routes
