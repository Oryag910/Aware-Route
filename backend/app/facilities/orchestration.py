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

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.facilities.diversity import select_diverse
from app.facilities.models import Facility, FacilityRequirement
from app.facilities.planning_deadline import PlanningDeadline
from app.facilities.scoring import (
    FacilityScore,
    is_fully_valid,
    rank_key,
    score_facility_requirements,
)
from app.facilities.spatial_index import FacilitySpatialIndex
from app.generation.engine import generate_routes
from app.generation.routes import GeneratedRoute
from app.routing.provider import Coordinate


# The app configures no explicit logging (no `logging.basicConfig`, no
# uvicorn `--log-config`), so a plain `logging.getLogger(__name__)`
# logger has no handler and an effective level of WARNING inherited
# from the unconfigured root logger -- `.info()` calls on it are
# silently dropped, never reaching stdout/Render's log capture.
# Uvicorn DOES configure "uvicorn.error" (and "uvicorn.access") with a
# stdout handler at INFO level on startup, so route_plan timing reuses
# that already-configured logger rather than adding any new logging
# setup of our own.
logger = logging.getLogger("uvicorn.error")

Shape = Literal["round", "out_and_back", "mix"]

# Overcomplete natural-match pool size, bounded independent of `count` so
# latency doesn't grow with the requested result count.
NATURAL_POOL_MULTIPLIER = 4
NATURAL_POOL_CEILING = 16

# No-facility mode skips Supabase, snapping, encounters, assignment, and
# every constrained planner -- the only extra cost of overcompleting its
# pool is ordinary graph-route construction, which is cheap enough to
# afford a modest cushion. Without this, `select_diverse` and the
# generic scorer had exactly `count` raw candidates to work with and no
# room to recover when one turned out overlapping/invalid -- the same
# problem `NATURAL_POOL_MULTIPLIER` already solves for the
# with-requirements path, just never applied when there were zero
# requirements to satisfy. Smaller than the facility-matching multiplier
# since natural matching has no hard constraint to hunt for -- distance
# tuning and turnaround diversity are the only sources of "wasted"
# candidates here.
NO_FACILITY_POOL_MULTIPLIER = 3
NO_FACILITY_POOL_CEILING = 10


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
    facility_index: FacilitySpatialIndex | None = None,
    timing: dict[str, float] | None = None,
) -> list[ScoredRoute]:
    """`facility_index` lets a caller scoring the same `facilities` list
    across many candidates/rescoring passes (see `plan_routes`) build the
    spatial index once and reuse it, instead of rebuilding it on every
    call -- if omitted, a private index is built for this call only.
    `timing`, if given, is passed through to `score_facility_requirements`
    to accumulate encounter/assignment timing across every route scored."""
    index = facility_index if facility_index is not None else FacilitySpatialIndex(facilities)
    scored: list[ScoredRoute] = []
    for route in routes:
        geometry = route.candidate.geometry
        distance_error = abs(route.candidate.distance_m - target_distance_m)
        facility_score = score_facility_requirements(
            geometry, requirements, facilities, facility_index=index, timing=timing
        )
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
    """`requirements=[]` skips every facility-matching cost (Supabase,
    snapping, encounters, assignment, constrained planners) -- but still
    asks for a modest overcomplete pool of ordinary graph-route
    candidates (see `NO_FACILITY_POOL_MULTIPLIER`/`_CEILING`) so
    `select_diverse` has real alternatives to choose `count` genuinely
    distinct routes from, instead of hoping exactly `count` raw
    candidates all survive construction and diversity filtering. Any
    real requirement widens the pool further so natural matching has
    enough candidates to find one that already happens to pass every
    requested stop.

    `count` here is the USER'S real requested final count -- the
    inflated `pool_size` below (e.g. 9 or 12 for a real `count=3` ask)
    is passed to `generate_routes` as its candidate-construction size,
    but `count` itself is passed separately as `requested_count` so the
    ordinary round-generator seam (`ROUND_GENERATOR=auto`, see
    `engine._round_generator_version`) picks a generator based on what
    the user actually asked for, not how many candidates were
    internally over-requested for this function's own diversity
    selection."""
    if not requirements:
        pool_size = min(
            NO_FACILITY_POOL_CEILING, max(count * NO_FACILITY_POOL_MULTIPLIER, count)
        )
        return generate_routes(
            graph, start, target_distance_m, shape, pool_size,
            result_count=pool_size, requested_count=count,
        )

    pool_size = min(NATURAL_POOL_CEILING, max(count * NATURAL_POOL_MULTIPLIER, count))
    return generate_routes(
        graph, start, target_distance_m, shape, pool_size,
        result_count=pool_size, requested_count=count,
    )


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


def _constraint_tier(item: ScoredRoute) -> tuple[int, int, int, float, float]:
    """The hard-constraint-quality prefix of `rank_key`: fully-valid bit,
    within-tolerance bit, requirements-satisfied count, worst-single-
    requirement range error, total range error across requirements. Two
    candidates in the same tier are equivalent on hard constraints --
    shape allocation may break ties between them. A candidate in a
    strictly better tier must never be displaced by shape quota (see
    `_select_mix_portfolio`).

    Deliberately stops short of `rank_key`'s remaining fields
    (`distance_error_m`, `quality_score`): once a route is inside the
    distance tolerance, small exact-distance differences may reasonably
    yield to shape diversity, but facility-constraint miss magnitude
    (range error) may not. Fully-valid routes all have zero range error,
    so this still allows useful diversity among them."""
    key = rank_key(
        item.route.candidate.geometry, item.distance_error_m, item.facility_score,
        item.quality_score,
    )
    return key[0], key[1], key[2], key[3], key[4]


def _select_diverse_within_tiers(scored: list[ScoredRoute], count: int) -> list[ScoredRoute]:
    """Tier-aware counterpart to a plain `select_diverse(scored, ...,
    count)` call for the non-mix (round / out_and_back) path.

    `select_diverse` is a single greedy scan over the WHOLE ranked
    list: once a higher-ranked item is skipped for overlapping an
    already-picked one, the scan keeps walking downward looking for
    ANY non-overlapping item to fill the remaining slots -- including
    one from a strictly worse `_constraint_tier` (e.g. outside distance
    tolerance) -- before ever getting a chance to reconsider the
    skipped, better-tier item. Measured root cause of a real count=5
    reliability gap: in constrained/narrow local topology, the FEW
    candidates that converge to a genuine round loop often trace
    near-identical streets (there's only one viable corridor), so two
    in-tolerance candidates can overlap enough to trigger exactly this
    -- the scan reaches an out-of-tolerance candidate ranked far below
    before the skipped in-tolerance one is ever reconsidered, and a
    tolerance-passing route is silently swapped for a failing one.

    This applies diversity WITHIN each tier instead: a whole tier that
    fits in the remaining slots is taken outright, and `select_diverse`
    only ever picks a SUBSET of a tier too large for the remaining
    slots -- so a candidate can never be selected ahead of a
    same-or-better tier candidate, only in place of one from its own
    tier. Mirrors `_select_mix_portfolio`'s already-proven tier-by-tier
    pattern (which guarantees the identical property for mix's shape
    allocation), minus the shape bookkeeping."""
    if count <= 1 or not scored:
        return scored[:count]

    def geometry_of(item: ScoredRoute) -> Any:
        return item.route.candidate.geometry

    chosen: list[ScoredRoute] = []
    index = 0
    while index < len(scored) and len(chosen) < count:
        tier_key = _constraint_tier(scored[index])
        tier_items: list[ScoredRoute] = []
        while index < len(scored) and _constraint_tier(scored[index]) == tier_key:
            tier_items.append(scored[index])
            index += 1

        slots_left = count - len(chosen)
        picked = (
            tier_items
            if len(tier_items) <= slots_left
            else select_diverse(tier_items, geometry_of, slots_left)
        )
        chosen.extend(picked)

    # Tier-then-diversity selection only decides membership -- restore
    # overall rank order for display, same as `_select_mix_portfolio`.
    rank_position = {id(item): index for index, item in enumerate(scored)}
    chosen.sort(key=lambda item: rank_position[id(item)])
    return chosen[:count]


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
# `plan_routes` receives plus the shared `PlanningDeadline` for this
# request, return extra `GeneratedRoute` candidates to fold into the same
# pool before scoring. Phase 3/4/5 register planners here instead of
# forking the orchestration flow.
ConstrainedPlanner = Callable[
    [Any, Coordinate, float, Shape, int, list[FacilityRequirement], list[Facility], PlanningDeadline],
    list[GeneratedRoute],
]


def _geometry_key(route: GeneratedRoute) -> tuple[tuple[float, float], ...]:
    return tuple((p.lat, p.lon) for p in route.candidate.geometry)


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
    """Natural-match-first, then PROGRESSIVE constrained planning.

    Each applicable constrained planner runs one at a time, folding its
    candidates into the pool and re-scoring immediately -- if that's
    already enough fully-valid candidates for `count`, later planners are
    skipped entirely rather than run unconditionally. This is a latency
    optimization only: the final scorer/ranker (`rank_key`, applied by
    `score_candidates`) stays the sole authority on which candidates are
    "good," so early exit can never promote a worse hard-constraint tier
    just because it arrived first -- it only avoids paying for search
    effort that a stronger tier already made unnecessary.

    The `PlanningDeadline` is started at the very top of this call --
    covering natural generation and scoring too, not just constrained
    search -- so a slow natural phase leaves correspondingly less
    budget for constrained planners rather than getting the full 25s on
    top. It is a COOPERATIVE budget, not a hard preemptive cutoff:
    checked before each expensive graph-routing operation in the
    constrained planners (see `app/facilities/planning_deadline.py` and
    `app/facilities/round_planner.py`/`oab_planner.py`), so actual
    overshoot is bounded by roughly one already-running synchronous
    graph operation, not by the budget itself. If the deadline expires
    mid-search, whatever candidates were already built are kept and
    scored normally; the budget limits search effort, never correctness
    labeling.
    """
    deadline = PlanningDeadline()
    total_start = time.perf_counter()

    # Built once and reused for every scoring pass below (natural, and
    # every progressive-planner rescore) -- the facility catalog never
    # changes mid-request, so there's no reason to rebuild the spatial
    # index per candidate or per rescore (see FacilitySpatialIndex).
    facility_index = FacilitySpatialIndex(facilities)
    # Accumulates encounter-finding/assignment time across every
    # scoring pass sharing this one dict -- see score_facility_requirements.
    scoring_timing: dict[str, float] = {}

    pool = natural_match_pool(graph, start, target_distance_m, shape, count, requirements)
    natural_generation_s = time.perf_counter() - total_start
    natural_candidates = len(pool)
    natural_segments = sum(max(0, len(r.candidate.geometry) - 1) for r in pool)

    score_start = time.perf_counter()
    scored = score_candidates(
        pool, target_distance_m, requirements, facilities,
        facility_index=facility_index, timing=scoring_timing,
    )
    natural_scoring_s = time.perf_counter() - score_start

    fully_valid_natural = sum(1 for s in scored if s.fully_valid)
    fully_valid_count = fully_valid_natural

    planner_timings: dict[str, float] = {}

    if constrained_planners and requirements and fully_valid_count < count:
        combined_pool = pool
        existing_keys = {_geometry_key(r.route) for r in scored}

        for planner in constrained_planners:
            if deadline.expired():
                break

            planner_start = time.perf_counter()
            new_candidates = planner(
                graph, start, target_distance_m, shape, count, requirements, facilities,
                deadline,
            )
            planner_timings[planner.__name__] = time.perf_counter() - planner_start

            new_routes = [r for r in new_candidates if _geometry_key(r) not in existing_keys]
            if not new_routes:
                continue

            combined_pool = combined_pool + new_routes
            existing_keys |= {_geometry_key(r) for r in new_routes}

            scored = score_candidates(
                combined_pool, target_distance_m, requirements, facilities,
                facility_index=facility_index, timing=scoring_timing,
            )
            fully_valid_count = sum(1 for s in scored if s.fully_valid)

            if fully_valid_count >= count:
                # Progressive early exit: the pool is already sufficient,
                # so the remaining planners' expensive search is skipped.
                break

    total_s = time.perf_counter() - total_start
    # Computed unconditionally regardless of whether constrained planners
    # ran -- the deadline covers the WHOLE call (including natural
    # generation/scoring, see above), so a slow natural phase alone can
    # legitimately exhaust the budget even though the graph-search
    # cooperative checks (which only gate constrained-planner work) never
    # got a chance to observe it. This field means "was the budget spent
    # by the time we finished," not "did search work get cut off."
    budget_exhausted = deadline.expired()

    planner_timing_str = " ".join(f"{name}_s={t:.3f}" for name, t in planner_timings.items())
    logger.info(
        "route_plan timing requirements=%d shape=%s facilities=%d "
        "natural_candidates=%d natural_segments=%d natural_generation_s=%.3f "
        "natural_scoring_s=%.3f encounter_scoring_s=%.3f assignment_s=%.3f "
        "%stotal_s=%.3f fully_valid_natural=%d "
        "fully_valid_final=%d budget_exhausted=%s",
        len(requirements),
        shape,
        len(facilities),
        natural_candidates,
        natural_segments,
        natural_generation_s,
        natural_scoring_s,
        scoring_timing.get("encounter_s", 0.0),
        scoring_timing.get("assignment_s", 0.0),
        planner_timing_str + " " if planner_timing_str else "",
        total_s,
        fully_valid_natural,
        fully_valid_count,
        budget_exhausted,
    )

    if shape == "mix":
        return _select_mix_portfolio(scored, count)

    return _select_diverse_within_tiers(scored, count)
