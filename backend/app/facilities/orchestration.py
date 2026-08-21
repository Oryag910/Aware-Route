"""Natural-match-first route planning for the generic `/routes` endpoint.

Phase 2 of the PR #18 rebuild: generate an ordinary, shape-appropriate
candidate pool exactly the way the no-facility path already does, then
score every candidate against the generic facility matcher. No facility
snapping happens here -- `find_facility_encounters` (via
`score_facility_requirements`) works directly off finished route
geometry, so natural matching costs nothing extra when
`facility_requirements=[]` beyond the trivial early-return in
`score_facility_requirements`.

`plan_routes` is deliberately the ONLY seam constrained planners
(multi-facility Polygon round / out-and-back, added in later phases)
need to extend: they contribute additional `GeneratedRoute` candidates
into the same pool before scoring, so one scorer/ranker stays
authoritative for both natural and constrained candidates.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.facilities.diversity import select_diverse
from app.facilities.models import Facility, FacilityRequirement
from app.facilities.scoring import (
    FacilityScore,
    is_fully_valid,
    rank_key,
    score_facility_requirements,
)
from app.generation.engine import generate_routes
from app.generation.routes import GeneratedRoute
from app.routing.provider import Coordinate


Shape = Literal["round", "out_and_back", "mix"]

# Overcomplete natural-match pool size, bounded independent of `count` so
# latency doesn't grow with the requested result count.
NATURAL_POOL_MULTIPLIER = 4
NATURAL_POOL_CEILING = 16


@dataclass(frozen=True)
class ScoredRoute:
    route: GeneratedRoute
    distance_error_m: float
    facility_score: FacilityScore
    quality_score: float
    fully_valid: bool


def _quality_score(route: GeneratedRoute) -> float:
    """Cheap route-quality proxy: less edge reuse and higher pedestrian
    share is better. Deliberately simple -- this is a tie-breaker tier
    (see `rank_key`), not the primary ranking signal.

    Edge reuse is zeroed for out_and_back: retracing the outbound leg on
    the return is that shape's defining, expected feature (an OAB's
    edge_reuse_ratio is inherently ~0.5), not a defect -- see
    `app/generation/quality.py`'s `edge_reuse_ratio` docstring. Counting
    it uniformly would unfairly penalize every OAB candidate relative to
    a round candidate whenever this score is compared across shapes
    (e.g. `_select_mix_portfolio`'s cross-shape backfill below), the
    exact "concrete OAB punished for legitimate retracing" failure mode
    this scorer must avoid.
    """
    quality = route.quality
    reuse_penalty = 0.0 if route.shape == "out_and_back" else quality.edge_reuse_ratio
    return reuse_penalty + (1.0 - quality.pedestrian_share)


def score_candidates(
    routes: list[GeneratedRoute],
    target_distance_m: float,
    requirements: list[FacilityRequirement],
    facilities: list[Facility],
) -> list[ScoredRoute]:
    scored: list[ScoredRoute] = []
    for route in routes:
        geometry = route.candidate.geometry
        distance_error = abs(route.candidate.distance_m - target_distance_m)
        facility_score = score_facility_requirements(geometry, requirements, facilities)
        quality = _quality_score(route)
        scored.append(
            ScoredRoute(
                route=route,
                distance_error_m=distance_error,
                facility_score=facility_score,
                quality_score=quality,
                fully_valid=is_fully_valid(geometry, distance_error, facility_score),
            )
        )

    scored.sort(
        key=lambda s: rank_key(
            s.route.candidate.geometry,
            s.distance_error_m,
            s.facility_score,
            s.quality_score,
        )
    )
    return scored


def natural_match_pool(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
    requirements: list[FacilityRequirement],
) -> list[GeneratedRoute]:
    """`requirements=[]` is a direct fast path: exactly `count` candidates,
    identical to the pre-PR#18 no-facility call shape -- no latency cost
    from a facility search nobody asked for. Any real requirement widens
    the pool so natural matching has enough candidates to find one that
    already happens to pass every requested stop."""
    if not requirements:
        return generate_routes(graph, start, target_distance_m, shape, count)

    pool_size = min(NATURAL_POOL_CEILING, max(count * NATURAL_POOL_MULTIPLIER, count))
    return generate_routes(graph, start, target_distance_m, shape, pool_size)


# count -> (round, out_and_back) target allocation for "mix" requests
# with real candidates of both shapes available. Gracefully backfills
# from the overall best-ranked remainder when one shape is short --
# hard-constraint/quality rank (via `rank_key`) always outranks hitting
# the exact portfolio quota.
MIX_SHAPE_ALLOCATION: dict[int, tuple[int, int]] = {
    1: (1, 0),
    2: (1, 1),
    3: (2, 1),
    4: (2, 2),
    5: (3, 2),
}


def _constraint_tier(item: ScoredRoute) -> tuple[int, int, int]:
    """The hard-constraint-quality prefix of `rank_key`: fully-valid bit,
    within-tolerance bit, requirements-satisfied count. Two candidates in
    the same tier are equivalent on hard constraints -- shape allocation
    may break ties between them. A candidate in a strictly better tier
    must never be displaced by shape quota (see `_select_mix_portfolio`)."""
    key = rank_key(
        item.route.candidate.geometry, item.distance_error_m, item.facility_score,
        item.quality_score,
    )
    return key[0], key[1], key[2]


def _select_mix_portfolio(scored: list[ScoredRoute], count: int) -> list[ScoredRoute]:
    """`scored` is already sorted by `rank_key`, so equal-tier candidates
    are contiguous. Shape allocation (`MIX_SHAPE_ALLOCATION`) is applied
    tier-by-tier, best tier first: a whole tier that fits in the
    remaining slots is taken outright (no shape filtering), and shape
    diversity is only used to pick a subset when a tier is larger than
    the remaining slots. This guarantees hard-constraint quality always
    outranks mix-shape diversity -- a partial/worse-tier candidate can
    never bump a better-tier one just to fill a round/OAB quota."""
    if count <= 1 or not scored:
        return scored[:count]

    target_round, target_oab = MIX_SHAPE_ALLOCATION.get(
        count, (count - count // 2, count // 2)
    )

    def geometry_of(item: ScoredRoute) -> Any:
        return item.route.candidate.geometry

    chosen: list[ScoredRoute] = []
    remaining_round, remaining_oab = target_round, target_oab

    index = 0
    while index < len(scored) and len(chosen) < count:
        tier_key = _constraint_tier(scored[index])
        tier_items: list[ScoredRoute] = []
        while index < len(scored) and _constraint_tier(scored[index]) == tier_key:
            tier_items.append(scored[index])
            index += 1

        slots_left = count - len(chosen)
        if len(tier_items) <= slots_left:
            picked = tier_items
        else:
            # This tier is a tie on hard-constraint quality -- shape
            # diversity is a valid tie-breaker here, but never across tiers.
            tier_rounds = [s for s in tier_items if s.route.shape == "round"]
            tier_oabs = [s for s in tier_items if s.route.shape == "out_and_back"]
            picked = select_diverse(
                tier_rounds, geometry_of, min(remaining_round, slots_left)
            ) + select_diverse(tier_oabs, geometry_of, min(remaining_oab, slots_left))
            picked_ids = {id(item) for item in picked}
            if len(picked) < slots_left:
                for item in tier_items:
                    if len(picked) >= slots_left:
                        break
                    if id(item) in picked_ids:
                        continue
                    picked.append(item)
                    picked_ids.add(id(item))
            picked = picked[:slots_left]

        for item in picked:
            chosen.append(item)
            if item.route.shape == "round":
                remaining_round = max(0, remaining_round - 1)
            else:
                remaining_oab = max(0, remaining_oab - 1)

    # Tier/portfolio selection only decides membership -- restore overall
    # rank order for display.
    rank_position = {id(item): index for index, item in enumerate(scored)}
    chosen.sort(key=lambda item: rank_position[id(item)])
    return chosen[:count]


# Signature for a constrained-planner hook: given the same inputs
# `plan_routes` receives, return extra `GeneratedRoute` candidates to
# fold into the same pool before scoring. Phase 3/4/5 register planners
# here instead of forking the orchestration flow.
ConstrainedPlanner = Callable[
    [Any, Coordinate, float, Shape, int, list[FacilityRequirement], list[Facility]],
    list[GeneratedRoute],
]


def plan_routes(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
    requirements: list[FacilityRequirement],
    facilities: list[Facility],
    *,
    constrained_planners: list[ConstrainedPlanner] | None = None,
) -> list[ScoredRoute]:
    """Natural-match-first planning. When `constrained_planners` are
    given and natural matching doesn't yield `count` fully valid
    candidates, each planner's extra candidates are folded into the
    same pool before final scoring -- hard-constraint quality from
    either source always outranks a prettier natural match (see
    `rank_key`)."""
    pool = natural_match_pool(graph, start, target_distance_m, shape, count, requirements)
    scored = score_candidates(pool, target_distance_m, requirements, facilities)

    fully_valid_count = sum(1 for s in scored if s.fully_valid)
    if constrained_planners and requirements and fully_valid_count < count:
        extra: list[GeneratedRoute] = []
        for planner in constrained_planners:
            extra.extend(
                planner(
                    graph, start, target_distance_m, shape, count, requirements, facilities
                )
            )
        if extra:
            existing_keys = {
                tuple((p.lat, p.lon) for p in r.route.candidate.geometry) for r in scored
            }
            new_routes = [
                route
                for route in extra
                if tuple((p.lat, p.lon) for p in route.candidate.geometry)
                not in existing_keys
            ]
            scored = score_candidates(
                pool + new_routes, target_distance_m, requirements, facilities
            )

    if shape == "mix":
        return _select_mix_portfolio(scored, count)

    return select_diverse(scored, lambda s: s.route.candidate.geometry, count)
