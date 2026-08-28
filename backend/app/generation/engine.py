import os
from typing import Any, Literal, cast

from app.amenities.snapping import SnappedAmenity
from app.generation.amenity_first import through_amenities_pairs
from app.generation.length_tune import DEFAULT_TOLERANCE_M, tune_generator_pairs_to_target
from app.generation.out_and_back import out_and_back_pairs
from app.generation.polygon_amenity import polygon_loop_through_amenities_pairs
from app.generation.polygon_loop import polygon_loop_pairs
from app.generation.round_route import round_pairs
from app.generation.routes import GeneratedRoute, compute_quality
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import nearest_node, single_source_paths
from app.routing.provider import Coordinate, RouteCandidate


Shape = Literal["round", "out_and_back", "mix"]

RoundGenerator = Literal["v1", "polygon"]


def _round_generator_version() -> RoundGenerator:
    """Feature selector for ordinary round-pool generation: "v1"
    (default) or "polygon" (opt-in, staged for promotion -- see below).
    Sourced from the `ROUND_GENERATOR` environment variable so it can be
    flipped without a code change. Applies everywhere an ORDINARY round
    candidate pool is built for the generic `/routes` path -- explicit
    `shape="round"` AND the round component of `shape="mix"` (both go
    through `_round_pairs` below), plus `facilities.orchestration`'s
    overcomplete natural-match pools, since those call the same
    `generate_routes` seam. "out_and_back" is untouched. The deprecated
    `/routes/with-restroom` endpoint's amenity-aware branch further down
    this file also reads this flag for its own "round" case, unchanged
    from PR #16 -- but its "mix" case still hardcodes V1's
    `through_amenities_pairs` regardless, since that legacy contract is
    out of scope for this migration.

    Default has been "v1" since PR #16/#17 and STAYS "v1" here despite a
    full re-evaluation (this migration): polygon's geometry is
    substantially better on every measured axis (radial exposure,
    elongation, compactness, zero tuner-generated start spurs) and,
    after PR #25 replaced O(facilities x segments) facility-encounter
    scoring with `FacilitySpatialIndex` (removing what used to be the
    dominant latency cost on real multi-facility requests regardless of
    round generator), polygon's own generation-latency picture is far
    better than the original PR #16 opt-in gate (p95 2.27s): a targeted
    `reuse_penalty._reuse_penalty_weight` optimization (this migration)
    plus a within-tolerance-first ranking + bounded V1 top-up fix for a
    real reliability regression (see `_round_pairs`) brought the full
    537-scenario p95 down to 2.295s. That is CLOSE to but still above the
    project's historical p95<2.0s raw-generation gate -- concentrated in
    a small number of genuinely extreme scenarios (peninsula-tip start
    points at large target distances) where polygon's own multi-anchor
    search and V1's fallback turnaround search are each independently
    expensive, so the reliability fix's fallback stacks both costs on
    exactly those requests. Per this migration's own instructions: do
    not silently redefine the gate to ship the default anyway -- report
    the tradeoff (see docs/benchmarks.md) and leave the switch here.
    Every other gate (correctness, geometry, count reliability, facility
    regression, end-to-end latency against the frontend's 35s timeout)
    passes. Set `ROUND_GENERATOR=polygon` to opt in now; flipping this
    default to "polygon" is a one-line follow-up once either the
    remaining p95 gap is closed or the gate itself is deliberately
    revisited.
    """
    value = os.environ.get("ROUND_GENERATOR", "v1").strip().lower()
    return "polygon" if value == "polygon" else "v1"


# Below this many of polygon's own within-tolerance candidates, top up
# the pool with V1 (see `_round_pairs`). Deliberately the API's max
# product `count` (5), not the (often much larger) overcomplete pool
# size `_round_pairs` actually receives -- the goal is "enough genuinely
# in-tolerance candidates to satisfy any real request downstream," not
# "every candidate in the pool must be in tolerance." Narrow/constrained
# local topology (e.g. upper-Manhattan peninsulas) at larger target
# distances can leave several of polygon's 10 templates unable to close
# to within `DEFAULT_TOLERANCE_M` at any scale in its bounds -- measured
# via `scripts/benchmark_count_reliability.py`: round-shape "all 3
# returned within tolerance" dropped from V1's 99.4% to polygon-alone's
# 84.4% on the same commit, concentrated in exactly this geography/
# distance combination (see docs/benchmarks.md).
MIN_WITHIN_TOLERANCE_FLOOR = 5


def _tolerance_first(
    pairs: list[tuple[RouteCandidate, list[int]]], target_distance_m: float
) -> list[tuple[RouteCandidate, list[int]]]:
    """Rank a round pool within-tolerance-first, roundest-first within
    each tier. `polygon_loop_pairs` on its own ranks purely by
    isoperimetric quotient, which can rank an off-target-but-rounder
    candidate ahead of an in-tolerance one even when the pool has
    plenty of in-tolerance candidates to spare -- this is the second
    half of the fix `_round_pairs` needs (see `MIN_WITHIN_TOLERANCE_FLOOR`
    for the first): a caller that only keeps the top few (e.g. a bare
    `generate_candidates` call with no downstream re-ranking) must not
    have distance accuracy silently lose to roundness."""

    def sort_key(pair: tuple[RouteCandidate, list[int]]) -> tuple[int, float]:
        candidate, _node_path = pair
        within_tolerance = abs(candidate.distance_m - target_distance_m) <= DEFAULT_TOLERANCE_M
        return (0 if within_tolerance else 1, -isoperimetric_quotient(candidate.geometry))

    return sorted(pairs, key=sort_key)


def _dedup_pairs(
    pairs: list[tuple[RouteCandidate, list[int]]],
) -> list[tuple[RouteCandidate, list[int]]]:
    seen: set[tuple[tuple[float, float], ...]] = set()
    unique: list[tuple[RouteCandidate, list[int]]] = []
    for candidate, node_path in pairs:
        key = tuple((point.lat, point.lon) for point in candidate.geometry)
        if key in seen:
            continue
        seen.add(key)
        unique.append((candidate, node_path))
    return unique


def _round_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_distance_m: float,
    count: int,
    paths: dict[int, list[int]] | None,
) -> list[tuple[RouteCandidate, list[int]]]:
    """Shared round-pool seam: the ONE place that decides V1 vs polygon
    for an ordinary round candidate pool, per `_round_generator_version`.
    Used by both explicit `shape="round"` and `shape="mix"`'s round
    component below, so neither can silently diverge from the other --
    see `_round_generator_version`'s docstring for the PR #16/#17
    history this replaces (mix used to hardcode V1 regardless of the
    flag).

    When polygon is selected, its own pool is topped up with V1
    candidates whenever polygon alone can't supply
    `MIN_WITHIN_TOLERANCE_FLOOR` in-tolerance candidates, and the
    combined pool is ranked in-tolerance-first (see
    `MIN_WITHIN_TOLERANCE_FLOOR` and `_tolerance_first`) -- polygon's
    better geometry still wins whenever it actually has enough
    in-tolerance candidates to offer (the common case), and V1's
    spur-guaranteed convergence is the fallback exactly where polygon's
    never-splice-a-spur design can leave a scenario short, never a
    silent, unconditional blend of the two."""
    if _round_generator_version() != "polygon":
        return _tuned_pairs(graph, start_node, dists, "round", target_distance_m, count, paths)

    polygon_pairs = polygon_loop_pairs(graph, start_node, target_distance_m, count, paths)
    within_tolerance = sum(
        1
        for candidate, _node_path in polygon_pairs
        if abs(candidate.distance_m - target_distance_m) <= DEFAULT_TOLERANCE_M
    )
    tolerance_floor = min(count, MIN_WITHIN_TOLERANCE_FLOOR)
    if within_tolerance >= tolerance_floor:
        return _tolerance_first(polygon_pairs, target_distance_m)[:count]

    # Only ask V1 for enough candidates to close the shortfall, not a
    # full `count`-sized pool -- V1's own turnaround search already
    # scales with what it's asked for (`round_pairs` requests
    # `min(v1_count * 2, MAX_TURNAROUND_ATTEMPTS)` turnarounds), so
    # asking for fewer meaningfully cuts the extra Dijkstra work this
    # fallback pays on top of polygon's own (already-paid) cost.
    v1_count = max(1, tolerance_floor - within_tolerance)
    v1_pairs = _tuned_pairs(graph, start_node, dists, "round", target_distance_m, v1_count, paths)
    combined = _dedup_pairs(polygon_pairs + v1_pairs)
    return _tolerance_first(combined, target_distance_m)[:count]


def _tuned_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    shape: Literal["round", "out_and_back"],
    target_distance_m: float,
    count: int,
    paths: dict[int, list[int]] | None = None,
) -> list[tuple[RouteCandidate, list[int]]]:
    """Generate a shape's (candidate, node_path) pairs and drive them to
    target length. `paths` (single-source shortest paths from start) is
    reused across every tuning iteration to avoid re-running outbound
    Dijkstras."""

    def generate(radius_scale: float) -> list[tuple[RouteCandidate, list[int]]]:
        if shape == "round":
            return round_pairs(
                graph, start_node, dists, target_distance_m, count, radius_scale,
                paths, tolerance_m=DEFAULT_TOLERANCE_M,
            )
        return out_and_back_pairs(
            graph, start_node, dists, target_distance_m, count, radius_scale,
            paths,
        )

    return tune_generator_pairs_to_target(
        graph, start_node, dists, generate, target_distance_m, paths=paths
    )


def _to_routes(
    graph: Any,
    pairs: list[tuple[RouteCandidate, list[int]]],
    shape: Shape,
) -> list[GeneratedRoute]:
    return [
        GeneratedRoute(
            candidate=candidate,
            node_path=node_path,
            shape=shape,
            quality=compute_quality(graph, node_path, candidate, shape),
        )
        for candidate, node_path in pairs
    ]


def generate_routes(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
    *,
    snapped: list[SnappedAmenity] | None = None,
    min_range_m: float | None = None,
    max_range_m: float | None = None,
    result_count: int | None = None,
) -> list[GeneratedRoute]:
    """Generate length-tuned `GeneratedRoute`s (candidate + node_path +
    quality metrics) of the requested shape.

    Mirrors `generate_candidates`/`generate_amenity_aware` but returns
    the richer `GeneratedRoute` wrapper needed by the local scorer. When
    `snapped` (+ range) is given, amenity-passing routes are unioned in
    front of the ordinary shape-based pool, same ranking rules as
    `generate_amenity_aware`. "round"/"mix" pools are ranked roundest
    first by isoperimetric quotient; "out_and_back" is ranked by the
    turnaround-bearing diversity already applied in `out_and_back_pairs`.

    `count` is both "how many to construct per shape" and (when
    `result_count` is omitted) "how many to keep". Callers that want an
    overcomplete candidate pool for their OWN downstream selection (e.g.
    `app.facilities.orchestration`'s shape-balanced mix portfolio) pass a
    larger `result_count`: for "mix" this skips the roundest-first
    truncation (which would otherwise silently drop every out_and_back
    candidate -- round loops structurally score much higher on
    isoperimetric quotient than a there-and-back line, so ranking the
    combined pool by quotient before a caller can apply its own shape
    quota starves that quota of out_and_back candidates regardless of
    how large the pool is) and instead keeps the wider deduped union.
    """
    start_node = nearest_node(graph, start)
    # One Dijkstra from the start yields both distances and every outbound
    # path; reused across shapes, tuning iterations, and the amenity pool
    # so outbound legs never re-run a Dijkstra.
    dists, paths = single_source_paths(graph, start_node)

    amenity_aware = (
        snapped is not None and min_range_m is not None and max_range_m is not None
    )
    final_count = count if result_count is None else result_count

    if shape == "round":
        pool = _to_routes(
            graph,
            _round_pairs(graph, start_node, dists, target_distance_m, count, paths),
            "round",
        )
    elif shape == "out_and_back":
        pool = _to_routes(
            graph,
            _tuned_pairs(
                graph, start_node, dists, shape, target_distance_m, count, paths
            ),
            shape,
        )
    else:
        # "mix": union both shape pools, dedup, keep the count best by
        # roundness (mirrors the candidate-only path's ranking). The
        # round component goes through the SAME seam (`_round_pairs`) as
        # explicit shape="round" above, so ROUND_GENERATOR applies
        # consistently to both.
        round_pool = _to_routes(
            graph,
            _round_pairs(graph, start_node, dists, target_distance_m, count, paths),
            "round",
        )
        out_back_pool = _to_routes(
            graph,
            _tuned_pairs(
                graph, start_node, dists, "out_and_back", target_distance_m,
                count, paths,
            ),
            "out_and_back",
        )
        if result_count is None:
            # Ordinary ranked-and-truncated behaviour: nobody downstream
            # is doing shape-balanced portfolio selection, so pick the
            # single best-by-roundness `count`.
            combined = _dedup_routes(round_pool + out_back_pool)
            combined.sort(
                key=lambda route: route.quality.isoperimetric_quotient,
                reverse=True,
            )
        else:
            # A caller doing its own shape-balanced portfolio selection
            # (e.g. orchestration's mix quota) needs BOTH shapes
            # represented in the truncated pool it gets back. Simply
            # concatenating round_pool + out_back_pool and truncating
            # would silently drop every out_and_back candidate whenever
            # round_pool alone already fills `final_count` (round routes
            # are also far more likely to survive tuning on a dense
            # Manhattan grid) -- interleaving round-robin guarantees both
            # shapes get a fair share of the truncated slots.
            interleaved = [
                route
                for pair in zip(round_pool, out_back_pool, strict=False)
                for route in pair
            ]
            leftover = round_pool[len(out_back_pool):] + out_back_pool[len(round_pool):]
            combined = _dedup_routes(interleaved + leftover)
        pool = combined[:final_count]

    if not amenity_aware:
        return pool[:final_count]

    assert snapped is not None and min_range_m is not None and max_range_m is not None
    amenity_triples: list[tuple[RouteCandidate, list[int], Shape]]
    if shape == "round" and _round_generator_version() == "polygon":
        amenity_triples = cast(
            "list[tuple[RouteCandidate, list[int], Shape]]",
            polygon_loop_through_amenities_pairs(
                graph, start, target_distance_m, snapped, min_range_m, max_range_m, count,
            ),
        )
    else:
        amenity_triples = through_amenities_pairs(
            graph,
            start,
            target_distance_m,
            snapped,
            min_range_m,
            max_range_m,
            shape,
            count,
            dists,
            paths,
        )
    amenity_pool = [
        GeneratedRoute(
            candidate=candidate,
            node_path=node_path,
            shape=triple_shape,
            quality=compute_quality(graph, node_path, candidate, triple_shape),
        )
        for candidate, node_path, triple_shape in amenity_triples
    ]

    combined = _dedup_routes(amenity_pool + pool)

    amenity_keys = {
        tuple((point.lat, point.lon) for point in route.candidate.geometry)
        for route in amenity_pool
    }

    def sort_key(route: GeneratedRoute) -> tuple[int, float, float]:
        key = tuple((point.lat, point.lon) for point in route.candidate.geometry)
        is_fallback = 0 if key in amenity_keys else 1
        distance_error = abs(route.candidate.distance_m - target_distance_m)
        if shape == "out_and_back":
            return (is_fallback, 0.0, distance_error)
        # round/mix: prefer rounder routes so genuine loops outrank the
        # out-and-backs that only happen to fit the target distance better.
        return (is_fallback, -route.quality.isoperimetric_quotient, distance_error)

    combined.sort(key=sort_key)

    return combined[:count]


def generate_candidates(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
) -> list[RouteCandidate]:
    """Generate length-tuned route candidates of the requested shape.

    "round" and "out_and_back" run their respective generators; "mix"
    unions both pools, dedups, and keeps the `count` best by shape
    quality (roundest first). All output is length-tuned toward
    `target_distance_m`. Thin wrapper around `generate_routes` that
    drops the node_path/quality detail for callers that only need
    candidates.
    """
    routes = generate_routes(graph, start, target_distance_m, shape, count)
    return [route.candidate for route in routes]


def generate_amenity_aware(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    shape: Shape,
    count: int,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
) -> list[RouteCandidate]:
    """Amenity-aware variant of `generate_candidates`.

    Unions the amenity-passing pool from `generate_through_amenities`
    with the ordinary shape-based pool, dedups, and returns the best
    `count` -- amenity-passing candidates are listed first since they
    satisfy a strictly harder constraint, then the rest fill out the
    count ranked by distance accuracy (round/mix also weigh roundness).
    Thin wrapper around `generate_routes` that drops the node_path/
    quality detail for callers that only need candidates.
    """
    routes = generate_routes(
        graph,
        start,
        target_distance_m,
        shape,
        count,
        snapped=snapped,
        min_range_m=min_range_m,
        max_range_m=max_range_m,
    )
    return [route.candidate for route in routes]


def generate_polygon_loop_candidates(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
) -> list[RouteCandidate]:
    """Multi-anchor polygon-loop round-route generator (PR #15).

    Standalone entry point used directly by
    scripts/benchmark_polygon_loop.py for side-by-side V1-vs-V2
    comparison. `generate_routes`'s shared `_round_pairs` seam also
    calls `polygon_loop_pairs` directly (this function's own
    implementation, inlined) for both explicit `shape="round"` and
    `shape="mix"`'s round component whenever `ROUND_GENERATOR=polygon`
    is set (opt-in -- see `_round_generator_version` for why this isn't
    the default yet). "out_and_back" is unaffected by the flag
    regardless.
    """
    start_node = nearest_node(graph, start)
    _dists, paths = single_source_paths(graph, start_node)
    pairs = polygon_loop_pairs(graph, start_node, target_distance_m, count, paths)
    return [candidate for candidate, _node_path in pairs]


def generate_polygon_loop_amenity_candidates(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
    snapped: list[SnappedAmenity],
    min_range_m: float,
    max_range_m: float,
) -> list[RouteCandidate]:
    """Amenity-aware polygon-loop round-route generator (PR #16):
    routes an amenity as a WAYPOINT on one leg of a multi-anchor
    polygon loop (see `polygon_amenity.py`) instead of treating it as
    the route's turnaround the way V1's `generate_amenity_aware` does.

    Standalone entry point used directly by
    scripts/benchmark_polygon_amenity.py for side-by-side V1-vs-V2
    comparison. `generate_routes`'s `shape == "round"` amenity-aware
    branch (the deprecated `/routes/with-restroom` contract only) also
    calls `polygon_loop_through_amenities_pairs` (this function's own
    implementation, inlined) whenever `ROUND_GENERATOR=polygon` is set
    (opt-in -- see `_round_generator_version`). Unlike
    the non-amenity-aware seam (`_round_pairs`), this legacy branch's
    "mix" case is NOT migrated -- it still hardcodes V1
    `through_amenities_pairs` regardless of the flag, since
    `/routes/with-restroom` is out of scope for this migration.
    "out_and_back" is unaffected by the flag regardless.
    """
    triples = polygon_loop_through_amenities_pairs(
        graph, start, target_distance_m, snapped, min_range_m, max_range_m, count
    )
    return [candidate for candidate, _node_path, _shape in triples]


def _dedup_routes(routes: list[GeneratedRoute]) -> list[GeneratedRoute]:
    """Drop routes with identical geometry, preserving order."""
    seen: set[tuple[tuple[float, float], ...]] = set()
    unique: list[GeneratedRoute] = []

    for route in routes:
        key = tuple((point.lat, point.lon) for point in route.candidate.geometry)
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)

    return unique
