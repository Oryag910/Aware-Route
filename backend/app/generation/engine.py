from typing import Any, Literal

from app.amenities.snapping import SnappedAmenity
from app.generation.amenity_first import through_amenities_pairs
from app.generation.length_tune import tune_generator_pairs_to_target
from app.generation.out_and_back import out_and_back_pairs
from app.generation.round_route import round_pairs
from app.generation.routes import GeneratedRoute, compute_quality
from app.graph.distances import nearest_node, single_source_paths
from app.routing.provider import Coordinate, RouteCandidate


Shape = Literal["round", "out_and_back", "mix"]


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
                paths,
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
    """
    start_node = nearest_node(graph, start)
    # One Dijkstra from the start yields both distances and every outbound
    # path; reused across shapes, tuning iterations, and the amenity pool
    # so outbound legs never re-run a Dijkstra.
    dists, paths = single_source_paths(graph, start_node)

    amenity_aware = (
        snapped is not None and min_range_m is not None and max_range_m is not None
    )

    if shape in ("round", "out_and_back"):
        pool = _to_routes(
            graph,
            _tuned_pairs(
                graph, start_node, dists, shape, target_distance_m, count, paths
            ),
            shape,
        )
    else:
        # "mix": union both shape pools, dedup, keep the count best by
        # roundness (mirrors the candidate-only path's ranking).
        round_pool = _to_routes(
            graph,
            _tuned_pairs(
                graph, start_node, dists, "round", target_distance_m, count, paths
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
        combined = _dedup_routes(round_pool + out_back_pool)
        combined.sort(
            key=lambda route: route.quality.isoperimetric_quotient,
            reverse=True,
        )
        pool = combined[:count]

    if not amenity_aware:
        return pool[:count]

    assert snapped is not None and min_range_m is not None and max_range_m is not None
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
