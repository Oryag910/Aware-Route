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
    (see `rank_key`), not the primary ranking signal."""
    quality = route.quality
    return quality.edge_reuse_ratio + (1.0 - quality.pedestrian_share)


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

    return scored[:count]
