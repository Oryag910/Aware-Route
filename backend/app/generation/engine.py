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


AUTO_POLYGON_MAX_REQUESTED_COUNT = 3


def _round_generator_version(requested_count: int) -> RoundGenerator:
    """Selects which round-pool generator to use, via `ROUND_GENERATOR`:

    - `"polygon"`: always polygon, regardless of `requested_count`.
    - `"v1"`: always v1, regardless of `requested_count`.
    - `"auto"` (the default, including when unset): polygon when
      `requested_count <= AUTO_POLYGON_MAX_REQUESTED_COUNT`, v1
      otherwise.
    - any other value (e.g. a typo): falls back to `"v1"`.

    Applies everywhere an ordinary round candidate pool is built for the
    generic `/routes` path: explicit `shape="round"` and the round
    component of `shape="mix"` (both go through `_round_pairs` below),
    plus `facilities.orchestration`'s overcomplete natural-match pools,
    which call the same `generate_routes` seam. `shape="out_and_back"`
    is untouched. The deprecated `/routes/with-restroom` endpoint's
    amenity-aware branch reads this flag for its own "round" case; its
    "mix" case still hardcodes v1's `through_amenities_pairs`.

    `requested_count` must be the user's actual requested final route
    count (`RouteRequest.count`, 1-5, default 3), never an internal
    overcomplete candidate-pool size. `facilities.orchestration`'s
    natural-match pools over-request candidates for downstream
    diversity selection (e.g. 9 or 12 candidates for a real `count=3`
    ask, see `NO_FACILITY_POOL_MULTIPLIER`/`NATURAL_POOL_MULTIPLIER`),
    and selecting the generator off that inflated number would pick v1
    for the count=3 default whenever facility requirements are present.
    See `generate_routes`'s `requested_count` parameter and
    `_round_pairs` for how the real count is threaded down here,
    independent of the pool-size `count` those functions also carry.

    Polygon has better geometry and matches or beats v1 at count=3;
    v1 has better full-suite latency and better reliability at count=5.
    See docs/benchmarks.md for the measurements behind the `auto`
    policy and its predecessors.
    """
    value = os.environ.get("ROUND_GENERATOR", "auto").strip().lower()
    if value == "polygon":
        return "polygon"
    if value == "v1":
        return "v1"
    if value == "auto":
        return "polygon" if requested_count <= AUTO_POLYGON_MAX_REQUESTED_COUNT else "v1"
    return "v1"  # explicitly invalid value, so it never enables an unvalidated path


# Below this many of polygon's own within-tolerance candidates, top up
# the pool with V1 (see `_round_pairs`). Set to the API's max product
# `count` (5) rather than the often much larger overcomplete pool size
# `_round_pairs` actually receives: the goal is enough in-tolerance
# candidates to satisfy any real request, not every candidate in the
# pool being in tolerance. In narrow local topology (e.g. upper-
# Manhattan peninsulas) at larger target distances, several of
# polygon's 10 templates can fail to close within `DEFAULT_TOLERANCE_M`
# at any scale; see docs/benchmarks.md for the measured impact.
MIN_WITHIN_TOLERANCE_FLOOR = 5


def _tolerance_first(
    pairs: list[tuple[RouteCandidate, list[int]]], target_distance_m: float
) -> list[tuple[RouteCandidate, list[int]]]:
    """Rank a round pool within-tolerance-first, roundest-first within
    each tier. `polygon_loop_pairs` on its own ranks purely by
    isoperimetric quotient, which can rank an off-target-but-rounder
    candidate ahead of an in-tolerance one even when the pool has
    plenty of in-tolerance candidates to spare. A caller that only
    keeps the top few (e.g. a bare `generate_candidates` call with no
    downstream re-ranking) needs distance accuracy to win over
    roundness."""

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
    requested_count: int,
) -> list[tuple[RouteCandidate, list[int]]]:
    """Shared round-pool seam: the one place that decides v1 vs polygon
    for an ordinary round candidate pool, per `_round_generator_version`.
    Used by both explicit `shape="round"` and `shape="mix"`'s round
    component below, so the two can't diverge.

    `count` and `requested_count` are different numbers. `count` is how
    many round candidates to actually build (the candidate-pool size,
    which may be a caller's overcomplete pool, e.g. 9 or 12 for a real
    3-route ask; see `generate_routes`), and it's what's passed to
    `polygon_loop_pairs` and `MIN_WITHIN_TOLERANCE_FLOOR`'s fallback
    math below. `requested_count` is the user's actual final route
    count and is used only to pick the generator
    (`_round_generator_version`); it never affects how many candidates
    get built.

    When polygon is selected, its own pool is topped up with V1
    candidates whenever polygon alone can't supply
    `MIN_WITHIN_TOLERANCE_FLOOR` in-tolerance candidates, and the
    combined pool is ranked in-tolerance-first (see
    `MIN_WITHIN_TOLERANCE_FLOOR` and `_tolerance_first`). Polygon's
    better geometry wins whenever it has enough in-tolerance candidates
    to offer, which is the common case; v1's spur-guaranteed
    convergence only kicks in where polygon's never-splice-a-spur
    design leaves a scenario short."""
    if _round_generator_version(requested_count) != "polygon":
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
    # full `count`-sized pool. V1's own turnaround search already
    # scales with what it's asked for (`round_pairs` requests
    # `min(v1_count * 2, MAX_TURNAROUND_ATTEMPTS)` turnarounds), so
    # asking for fewer cuts the extra Dijkstra work this fallback pays
    # on top of polygon's own already-paid cost.
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
    requested_count: int | None = None,
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

    `count` is both "how many to construct per shape" and, when
    `result_count` is omitted, "how many to keep". Callers that want an
    overcomplete candidate pool for their own downstream selection (e.g.
    `app.facilities.orchestration`'s shape-balanced mix portfolio) pass a
    larger `result_count`. For "mix" this skips the roundest-first
    truncation and keeps the wider deduped union instead: round loops
    score much higher on isoperimetric quotient than a there-and-back
    line, so ranking the combined pool by quotient before a caller
    applies its own shape quota would starve that quota of out_and_back
    candidates regardless of pool size.

    `requested_count` is the user's actual requested final route count,
    used only by the round-generator seam (`_round_pairs` ->
    `_round_generator_version`) to pick v1 vs polygon in
    `ROUND_GENERATOR=auto` mode. It never changes how many candidates
    get built; that's still `count`/`result_count`. Defaults to `count`
    when omitted, which preserves every direct caller's existing
    behavior (`generate_candidates`, `generate_amenity_aware`,
    benchmark scripts, tests): none of them distinguish an overcomplete
    pool from a real ask, so `count` already is their real requested
    count. A caller that does build an overcomplete pool for its own
    downstream selection, currently only
    `facilities.orchestration.natural_match_pool`, must pass its real,
    pre-inflation count here explicitly, or auto mode would see the
    inflated pool size (e.g. 9 or 12 for a real count=3 ask) and pick
    v1 for what is actually the product's validated count=3 case.
    """
    effective_requested_count = count if requested_count is None else requested_count

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
            _round_pairs(
                graph, start_node, dists, target_distance_m, count, paths,
                effective_requested_count,
            ),
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
            _round_pairs(
                graph, start_node, dists, target_distance_m, count, paths,
                effective_requested_count,
            ),
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
            # (e.g. orchestration's mix quota) needs both shapes
            # represented in the truncated pool it gets back. Simply
            # concatenating round_pool + out_back_pool and truncating
            # would drop every out_and_back candidate whenever round_pool
            # alone already fills `final_count` (round routes are also
            # far more likely to survive tuning on a dense Manhattan
            # grid), so round-robin interleaving gives both shapes a
            # fair share of the truncated slots.
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
    if shape == "round" and _round_generator_version(effective_requested_count) == "polygon":
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
    `count`. Amenity-passing candidates are listed first since they
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
    `shape="mix"`'s round component whenever `ROUND_GENERATOR` selects
    polygon (see `_round_generator_version`). "out_and_back" is
    unaffected by the flag regardless.
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
    routes an amenity as a waypoint on one leg of a multi-anchor
    polygon loop (see `polygon_amenity.py`) instead of treating it as
    the route's turnaround the way v1's `generate_amenity_aware` does.

    Standalone entry point used directly by
    scripts/benchmark_polygon_amenity.py for side-by-side v1-vs-v2
    comparison. `generate_routes`'s `shape == "round"` amenity-aware
    branch (the deprecated `/routes/with-restroom` contract only) also
    calls `polygon_loop_through_amenities_pairs` (this function's own
    implementation, inlined) whenever `ROUND_GENERATOR` selects polygon
    (see `_round_generator_version`). Unlike the non-amenity-aware seam
    (`_round_pairs`), this legacy branch's "mix" case still hardcodes
    v1's `through_amenities_pairs` regardless of the flag; that path
    (`/routes/with-restroom`) is out of scope for this migration.
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
