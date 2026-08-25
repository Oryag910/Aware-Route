"""Deterministic one-to-one requirement <-> encounter assignment.

Given a variable-length set of typed requirements and the facility
encounters found on a finished route, solves a bipartite assignment so
that: kinds match exactly, each ENCOUNTER satisfies at most one
requirement, and the result never depends on requirement input order.

Two-phase, per `docs` in the PR #18 spec:

PHASE 1: maximum-cardinality matching restricted to in-range compatible
edges (same kind, mile marker inside the requested window), tie-broken by
minimum range-position/off-route cost.

PHASE 2: still-unmatched requirements are optionally paired with an
UNUSED compatible-kind encounter (any mile marker) as an explanatory
"closest miss" -- these stay `satisfied=False`. Phase 2 can never take an
encounter Phase 1 already used, so it can't steal a satisfiable match
from another requirement.

Implemented as min-cost-max-flow (`networkx.max_flow_min_cost`) on a
tiny bipartite graph -- deterministic, polynomial, and exact for the
request-size ceiling this product enforces (~20 requirements).
"""

from collections.abc import Callable

import networkx as nx

from app.facilities.models import FacilityEncounter, FacilityRequirement, RequirementResult


EdgeCost = Callable[[FacilityRequirement, FacilityEncounter], float | None]


# Off-route distance is a tie-breaker, not the primary signal -- a
# facility 1m off-route but 50m from the window midpoint should still
# lose to one exactly on-route but 60m from the midpoint. Kept small
# relative to typical range-error magnitudes (tens to thousands of
# meters).
OFF_ROUTE_WEIGHT = 0.05

# Encodes (rounded-to-millimeter primary cost, deterministic tie rank)
# into a single integer cost for the min-cost-flow solver, so ties in
# primary cost resolve by a fixed encounter ordering (facility id, then
# encounter index) rather than by requirement input order or solver
# implementation detail.
_TIE_BREAK_SCALE = 1_000_000


def _range_error_m(mile_marker_m: float, requirement: FacilityRequirement) -> float:
    if requirement.min_distance_m <= mile_marker_m <= requirement.max_distance_m:
        return 0.0
    if mile_marker_m < requirement.min_distance_m:
        return requirement.min_distance_m - mile_marker_m
    return mile_marker_m - requirement.max_distance_m


def _scaled_cost(base_cost_m: float, tie_rank: int) -> int:
    return int(round(base_cost_m * 1000)) * _TIE_BREAK_SCALE + tie_rank


def _min_cost_match(
    requirements: list[FacilityRequirement],
    encounters: list[FacilityEncounter],
    tie_rank: dict[int, int],
    edge_cost: EdgeCost,
) -> dict[str, FacilityEncounter]:
    """Max-cardinality, min-cost bipartite match. `edge_cost` returns
    None for an incompatible pair (no edge) or the pair's real-valued
    cost. Returns requirement id -> assigned encounter."""
    graph: nx.DiGraph = nx.DiGraph()
    source, sink = "__source__", "__sink__"
    has_edge = False

    for requirement in requirements:
        graph.add_edge(source, ("req", requirement.id), capacity=1, weight=0)
    for index, encounter in enumerate(encounters):
        graph.add_edge(("enc", index), sink, capacity=1, weight=0)

    for requirement in requirements:
        for index, encounter in enumerate(encounters):
            cost = edge_cost(requirement, encounter)
            if cost is None:
                continue
            has_edge = True
            graph.add_edge(
                ("req", requirement.id),
                ("enc", index),
                capacity=1,
                weight=_scaled_cost(cost, tie_rank[id(encounter)]),
            )

    if not has_edge:
        return {}

    flow = nx.max_flow_min_cost(graph, source, sink)

    assignment: dict[str, FacilityEncounter] = {}
    for requirement in requirements:
        for target, sent in flow.get(("req", requirement.id), {}).items():
            if sent > 0 and isinstance(target, tuple) and target[0] == "enc":
                assignment[requirement.id] = encounters[target[1]]
    return assignment


def assign_requirements(
    requirements: list[FacilityRequirement],
    encounters: list[FacilityEncounter],
) -> list[RequirementResult]:
    """Assign each requirement at most one encounter. Order of both
    input lists is irrelevant to the result -- encounters are given a
    fixed deterministic rank up front, independent of list order."""
    ordered_encounters = sorted(
        encounters, key=lambda e: (e.facility.id, e.encounter_index)
    )
    tie_rank = {id(e): rank for rank, e in enumerate(ordered_encounters)}

    def phase1_cost(
        requirement: FacilityRequirement, encounter: FacilityEncounter
    ) -> float | None:
        if encounter.facility.kind != requirement.kind:
            return None
        if not (
            requirement.min_distance_m
            <= encounter.mile_marker_m
            <= requirement.max_distance_m
        ):
            return None
        midpoint = (requirement.min_distance_m + requirement.max_distance_m) / 2.0
        return (
            abs(encounter.mile_marker_m - midpoint)
            + OFF_ROUTE_WEIGHT * encounter.distance_to_route_m
        )

    primary = _min_cost_match(requirements, ordered_encounters, tie_rank, phase1_cost)

    remaining_requirements = [r for r in requirements if r.id not in primary]
    used_encounters = set(primary.values())
    remaining_encounters = [
        e for e in ordered_encounters if e not in used_encounters
    ]

    def phase2_cost(
        requirement: FacilityRequirement, encounter: FacilityEncounter
    ) -> float | None:
        if encounter.facility.kind != requirement.kind:
            return None
        return _range_error_m(
            encounter.mile_marker_m, requirement
        ) + OFF_ROUTE_WEIGHT * encounter.distance_to_route_m

    fallback = _min_cost_match(
        remaining_requirements, remaining_encounters, tie_rank, phase2_cost
    )

    results: list[RequirementResult] = []
    for requirement in requirements:
        if requirement.id in primary:
            encounter = primary[requirement.id]
            results.append(
                RequirementResult(
                    requirement=requirement,
                    satisfied=True,
                    range_error_m=0.0,
                    encounter=encounter,
                )
            )
        elif requirement.id in fallback:
            encounter = fallback[requirement.id]
            results.append(
                RequirementResult(
                    requirement=requirement,
                    satisfied=False,
                    range_error_m=_range_error_m(
                        encounter.mile_marker_m, requirement
                    ),
                    encounter=encounter,
                )
            )
        else:
            results.append(
                RequirementResult(
                    requirement=requirement,
                    satisfied=False,
                    range_error_m=None,
                    encounter=None,
                )
            )

    return results
