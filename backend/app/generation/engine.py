from typing import Any, Literal

from app.generation.length_tune import tune_generator_to_target
from app.generation.out_and_back import out_and_back_pairs
from app.generation.round_route import round_pairs
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import nearest_node, single_source_distances
from app.routing.provider import Coordinate, RouteCandidate


Shape = Literal["round", "out_and_back", "mix"]


def _tuned_pool(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    shape: Literal["round", "out_and_back"],
    target_distance_m: float,
    count: int,
) -> list[RouteCandidate]:
    """Generate a shape's candidates and drive them to target length."""

    def generate(radius_scale: float) -> list[tuple[RouteCandidate, list[int]]]:
        if shape == "round":
            return round_pairs(
                graph, start_node, dists, target_distance_m, count, radius_scale
            )
        return out_and_back_pairs(
            graph, start_node, dists, target_distance_m, count, radius_scale
        )

    return tune_generator_to_target(
        graph, start_node, dists, generate, target_distance_m
    )


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
    `target_distance_m`.
    """
    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)

    if shape in ("round", "out_and_back"):
        pool = _tuned_pool(
            graph, start_node, dists, shape, target_distance_m, count
        )
        return pool[:count]

    # "mix": union both pools, dedup, keep the count best by roundness.
    round_pool = _tuned_pool(
        graph, start_node, dists, "round", target_distance_m, count
    )
    out_back_pool = _tuned_pool(
        graph, start_node, dists, "out_and_back", target_distance_m, count
    )

    combined = _dedup(round_pool + out_back_pool)
    combined.sort(
        key=lambda candidate: isoperimetric_quotient(candidate.geometry),
        reverse=True,
    )
    return combined[:count]


def _dedup(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    """Drop candidates with identical geometry, preserving order."""
    seen: set[tuple[tuple[float, float], ...]] = set()
    unique: list[RouteCandidate] = []

    for candidate in candidates:
        key = tuple((point.lat, point.lon) for point in candidate.geometry)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique
