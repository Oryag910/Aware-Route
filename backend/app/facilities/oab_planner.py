"""Bounded, deterministic constrained out-and-back planner (PR #18 Phase 4).

`app/facilities/orchestration.py`'s `plan_routes` already re-scores every
candidate it's handed -- including candidates from this module -- against
finished route geometry using the authoritative Phase 1 matcher
(`find_facility_encounters` + `assign_requirements`, via
`score_facility_requirements`). That means this planner does not need to
do its own final placement validation: it only needs to PROPOSE
spatially plausible out-and-back candidates that are LIKELY to pass the
requested facilities at roughly the right cumulative distance. The
generic scorer decides `satisfied`/`fully_valid` downstream.

The route shape mirrors `app/generation/out_and_back.py`'s
`out_and_back_path` (`outbound + reversed(outbound)[1:]`), generalized to
a multi-waypoint outbound leg that threads through one snapped facility
per requirement (ordered by radial position along the leg) before
extending to a turnaround near `target_distance_m / 2`.

Search is bounded at every stage (small constants below) rather than
exhaustive: a handful of candidate corridor directions
(`select_turnarounds`, reused as-is rather than reinventing bearing-sector
logic), a small per-requirement shortlist of candidate facilities, and a
narrow beam search over shortlist choices -- all of it over CHEAP
distance/bearing heuristics. Only a small capped number of resulting
plans get a REAL graph build (`reuse_penalized_path` legs), which is the
expensive step. This keeps wall-clock roughly linear in requirement count
instead of combinatorial.

Never imports `app/generation/length_tune.py` (V1's spur-splicing
tuner) -- forbidden for this planner, see module docstring cross-refs in
the PR #18 spec. Never imports/modifies `app/generation/amenity_first.py`
(legacy single-amenity architecture, not generalized here).
"""

from typing import Any, Literal

from app.facilities.models import Facility, FacilityRequirement, SnappedFacility
from app.facilities.planning_deadline import PlanningDeadline
from app.facilities.snapping import snap_facilities
from app.generation.reuse_penalty import edge_pairs, reuse_penalized_path
from app.generation.routes import GeneratedRoute, compute_quality
from app.generation.turnarounds import select_turnarounds
from app.graph.distances import (
    nearest_node,
    outbound_path,
    single_source_distances,
    single_source_paths,
)
from app.graph.model import node_coordinate, path_distance_m, path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.geometry import bearing_deg
from app.routing.provider import Coordinate


Shape = Literal["round", "out_and_back", "mix"]


# --- Bounded-search constants -----------------------------------------
#
# Measured (not just guessed) with a throwaway timing script against the
# real committed Manhattan graph, start=(40.7812, -73.9665),
# target_distance_m=8000, at these exact constants: a 2-requirement call
# took ~0.4s wall-clock (4 `reuse_penalized_path` calls, 2
# `single_source_distances` calls) and a 6-requirement call took ~0.26s
# (30 `reuse_penalized_path` calls, 0 `single_source_distances` calls --
# the longer waypoint chain already met target_distance_m/2 before an
# extension leg was needed). Real-build call counts stayed linear in
# requirement count (bounded by MAX_FULL_BUILDS_PER_CALL x
# (requirements - 1), never by corridor count x requirement count), as
# intended -- not a rigorous benchmark, but enough to confirm the shape
# is sub-second and roughly linear rather than exponential. If later
# benchmarking (e.g. `scripts/benchmark_suite.py`) shows these are too
# loose/tight for a given requirement count, tighten here first before
# touching the algorithm shape.

# Candidate corridor directions probed per call (`select_turnarounds`
# count). Mirrors `out_and_back_pairs`' typical fan-out.
CORRIDOR_COUNT = 6

# Per-requirement shortlist size when ranking snapped facilities by
# cheap distance/bearing heuristics (step 6b in the spec).
SHORTLIST_SIZE = 4

# Beam width carried between requirements while building an ordered
# waypoint sequence per corridor.
BEAM_WIDTH = 6

# Complete beam plans taken per corridor direction for a REAL graph
# build attempt.
PLANS_PER_CORRIDOR = 3

# Global cap on real graph builds across the whole call, independent of
# requirement count or corridor count.
MAX_FULL_BUILDS_PER_CALL = 8

# Angular tolerance (degrees) used both when shortlisting candidate
# facilities against a corridor bearing and when extending toward a
# turnaround -- generous enough that a mostly-straight corridor doesn't
# reject every real street, tight enough to keep the chain roughly
# coherent instead of zigzagging across the city.
SHORTLIST_ANGULAR_TOLERANCE_DEG = 60.0
TURNAROUND_ANGULAR_TOLERANCE_DEG = 90.0

# Result cap independent of `count` -- `plan_routes` re-scores/re-ranks
# everything itself, so returning somewhat more than `count` candidates
# is fine/expected.
RESULT_CEILING = 8


def _adaptive_build_budget(requirement_count: int) -> int:
    """How many of `MAX_FULL_BUILDS_PER_CALL`'s expensive real-graph
    builds this call gets, based on requirement count.

    Mirrors `round_planner._adaptive_build_budget`'s rationale: the Aug
    25 facility stress benchmark shows a single genuinely-hard
    requirement is reliably solved at the full budget, while 2+
    simultaneous hard requirements already succeed in only 0-2.8% of
    stress cases even at full budget -- so the full budget is preserved
    where it's proven to matter and shrunk where extra builds mostly
    just add latency.
    """
    if requirement_count <= 1:
        return MAX_FULL_BUILDS_PER_CALL
    if requirement_count == 2:
        return 5
    return 3


def _angular_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def _radial_position(requirement: FacilityRequirement, target_distance_m: float) -> float:
    """Approximate distance-from-start the outbound leg needs to reach to
    satisfy `requirement`, whichever half of the out-and-back it lands
    on. Heuristic only -- the real matcher (Phase 1) decides correctness
    on finished geometry."""
    midpoint = (requirement.min_distance_m + requirement.max_distance_m) / 2.0
    return min(midpoint, target_distance_m - midpoint)


def _shortlist_for_requirement(
    requirement: FacilityRequirement,
    radial_position: float,
    corridor_bearing: float,
    start_coord: Coordinate,
    dists: dict[int, float],
    snapped_by_kind: dict[str, list[SnappedFacility]],
    node_coords: dict[int, Coordinate],
) -> list[tuple[int, float]]:
    """Bounded shortlist of (node_id, cheap_score) for one requirement,
    ranked by combined distance-from-target + angular-penalty cost. Never
    triggers a real graph build."""
    candidates = snapped_by_kind.get(requirement.kind, ())
    scored: list[tuple[float, int]] = []

    for snapped in candidates:
        node = snapped.node_id
        dist = dists.get(node)
        if dist is None:
            continue

        bearing = bearing_deg(start_coord, node_coords[node])
        angular_gap = _angular_distance(bearing, corridor_bearing)
        if angular_gap > SHORTLIST_ANGULAR_TOLERANCE_DEG:
            continue

        distance_cost = abs(dist - radial_position)
        # Normalize the angular penalty into meters-ish scale so neither
        # term dominates purely from unit mismatch: ~10m per degree of
        # angular gap is a mild tie-breaker, not a hard cutoff (the hard
        # cutoff is the tolerance check above).
        angular_penalty = angular_gap * 10.0
        score = distance_cost + angular_penalty
        scored.append((score, node))

    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [(node, score) for score, node in scored[:SHORTLIST_SIZE]]


BeamPlan = tuple[float, tuple[int, ...]]


def _beam_search(
    ordered_requirements: list[FacilityRequirement],
    corridor_bearing: float,
    start_coord: Coordinate,
    dists: dict[int, float],
    snapped_by_kind: dict[str, list[SnappedFacility]],
    node_coords: dict[int, Coordinate],
    target_distance_m: float,
) -> list[BeamPlan]:
    """Beam search over per-requirement shortlist choices, building an
    ordered waypoint-node sequence one requirement at a time. Rejects a
    shortlist candidate if it's already used earlier in the same partial
    plan. Entirely over cheap heuristic costs -- no graph build here."""
    beams: list[BeamPlan] = [(0.0, ())]

    for requirement in ordered_requirements:
        radial_position = _radial_position(requirement, target_distance_m)
        shortlist = _shortlist_for_requirement(
            requirement, radial_position, corridor_bearing, start_coord, dists,
            snapped_by_kind, node_coords,
        )
        if not shortlist:
            return []

        next_beams: list[BeamPlan] = []
        for cost_so_far, nodes_so_far in beams:
            used = set(nodes_so_far)
            for node, cost in shortlist:
                if node in used:
                    continue
                next_beams.append((cost_so_far + cost, nodes_so_far + (node,)))

        if not next_beams:
            return []

        next_beams.sort(key=lambda plan: (plan[0], plan[1]))
        beams = next_beams[:BEAM_WIDTH]

    return beams


def _extend_to_turnaround(
    graph: Any,
    last_node: int,
    cumulative_so_far_m: float,
    target_half_m: float,
    start_coord: Coordinate,
    corridor_bearing: float,
    used_pairs: set[frozenset[int]],
) -> list[int] | None:
    """Real (bounded) Dijkstra from `last_node`, picking an extension
    target whose cumulative distance from start lands closest to
    `target_half_m`, filtered to roughly continue the corridor bearing.
    Returns the reuse-penalized leg node list (including `last_node` as
    its first element), or None if no extension leg is reachable/needed
    (waypoints already meet or exceed `target_half_m`)."""
    if cumulative_so_far_m >= target_half_m:
        return None

    local_dists = single_source_distances(graph, last_node)

    best_node: int | None = None
    best_gap = float("inf")

    for node, dist_from_last in local_dists.items():
        if node == last_node:
            continue
        projected_total = cumulative_so_far_m + dist_from_last
        node_coord = node_coordinate(graph, node)
        bearing = bearing_deg(start_coord, node_coord)
        if _angular_distance(bearing, corridor_bearing) > TURNAROUND_ANGULAR_TOLERANCE_DEG:
            continue

        gap = abs(projected_total - target_half_m)
        if gap < best_gap:
            best_gap = gap
            best_node = node

    if best_node is None:
        return None

    return reuse_penalized_path(graph, last_node, best_node, used_pairs)


def _dedup_key(route: GeneratedRoute) -> tuple[tuple[float, float], ...]:
    return tuple((p.lat, p.lon) for p in route.candidate.geometry)


def plan_constrained_out_and_back(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Literal["round", "out_and_back", "mix"],
    count: int,
    requirements: list[FacilityRequirement],
    facilities: list[Facility],
    deadline: PlanningDeadline | None = None,
) -> list[GeneratedRoute]:
    """Constrained out-and-back planner: threads a variable-length list of
    typed facility requirements onto an out-and-back's outbound corridor.

    Returns `[]` immediately if `shape` can't use an out-and-back, or if
    there are no requirements to place (a no-facility request must never
    invoke this planner's search machinery). See module docstring for the
    bounded-search algorithm.

    `deadline` is a cooperative, shared-across-planners wall-clock
    budget (see `app/facilities/planning_deadline.py`) checked before
    each expensive real graph build; if unset, a fresh default-budget
    deadline is used so this planner remains safely callable on its own
    (e.g. from tests)."""
    if shape not in ("out_and_back", "mix"):
        return []
    if not requirements:
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
    dists, paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    node_coords = {entry.node_id: node_coordinate(graph, entry.node_id) for entry in snapped}

    ordered_requirements = sorted(
        requirements,
        key=lambda r: (_radial_position(r, target_distance_m), r.id),
    )

    corridor_turnarounds = select_turnarounds(
        graph, start_node, dists, target_distance_m / 2.0, CORRIDOR_COUNT, prefer_straight=True
    )

    results: list[GeneratedRoute] = []
    seen_geometries: set[tuple[tuple[float, float], ...]] = set()
    full_builds_attempted = 0
    build_budget = _adaptive_build_budget(len(requirements))

    for corridor_node, _corridor_dist in corridor_turnarounds:
        if full_builds_attempted >= build_budget or deadline.expired():
            break

        corridor_bearing = bearing_deg(start_coord, node_coordinate(graph, corridor_node))

        beam_plans = _beam_search(
            ordered_requirements, corridor_bearing, start_coord, dists, snapped_by_kind,
            node_coords, target_distance_m,
        )
        if not beam_plans:
            continue

        for _cost, waypoint_nodes in beam_plans[:PLANS_PER_CORRIDOR]:
            if full_builds_attempted >= build_budget or deadline.expired():
                break
            full_builds_attempted += 1

            route = _build_plan(
                graph, start_node, list(waypoint_nodes), paths, target_distance_m,
                start_coord, corridor_bearing,
            )
            if route is None:
                continue

            key = _dedup_key(route)
            if key in seen_geometries:
                continue
            seen_geometries.add(key)
            results.append(route)

    ceiling = min(RESULT_CEILING, max(count * 2, count))
    return results[:ceiling]


def _build_plan(
    graph: Any,
    start_node: int,
    waypoint_nodes: list[int],
    paths: dict[int, list[int]],
    target_distance_m: float,
    start_coord: Coordinate,
    corridor_bearing: float,
) -> GeneratedRoute | None:
    """Real build for one ordered waypoint sequence: start -> waypoints in
    order -> (optional) turnaround extension -> mirrored return leg."""
    if not waypoint_nodes:
        return None

    try:
        first_leg = outbound_path(graph, start_node, waypoint_nodes[0], paths)
    except RouteNotFoundError:
        return None

    outbound_full_path = list(first_leg)
    used = edge_pairs(outbound_full_path)

    for waypoint in waypoint_nodes[1:]:
        leg = reuse_penalized_path(graph, outbound_full_path[-1], waypoint, used)
        if leg is None:
            return None
        outbound_full_path += leg[1:]
        used |= edge_pairs(leg)

    cumulative_so_far_m = path_distance_m(graph, outbound_full_path)
    target_half_m = target_distance_m / 2.0

    extension = _extend_to_turnaround(
        graph,
        outbound_full_path[-1],
        cumulative_so_far_m,
        target_half_m,
        start_coord,
        corridor_bearing,
        used,
    )
    if extension is not None and len(extension) > 1:
        outbound_full_path += extension[1:]

    full_node_path = outbound_full_path + list(reversed(outbound_full_path))[1:]

    if len(full_node_path) < 2:
        return None
    if full_node_path[0] != start_node or full_node_path[-1] != start_node:
        return None  # defensive -- unreachable by construction

    candidate = path_to_candidate(graph, full_node_path)
    quality = compute_quality(graph, full_node_path, candidate, "out_and_back")
    return GeneratedRoute(
        candidate=candidate, node_path=full_node_path, shape="out_and_back", quality=quality
    )
