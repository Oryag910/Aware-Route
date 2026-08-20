"""Greedy route-segment diversity preference for final candidate selection.

A clean, standalone extraction of `scripts/benchmark_suite.py`'s
rendered-geometry segment-overlap (Jaccard) idea for production use --
deliberately NOT importing that script's private helpers into app code
(the benchmark stays free to evolve its own reporting logic
independently).

Diversity is the LOWEST-priority signal: `select_diverse` only ever
reorders which candidates get picked from an already hard-constraint/
quality-ranked list (see `app/facilities/scoring.py`'s `rank_key`) --
it never promotes a worse-ranked candidate over a better one, and it
never returns fewer than the best available count merely because every
remaining alternative overlaps.
"""

from collections.abc import Callable
from typing import TypeVar

from app.routing.provider import RoutePoint


SEGMENT_COORD_PRECISION = 6
DEFAULT_MAX_OVERLAP = 0.80

SegmentSignature = frozenset[tuple[tuple[float, float], tuple[float, float]]]

T = TypeVar("T")


def route_segment_signature(geometry: tuple[RoutePoint, ...]) -> SegmentSignature:
    """Undirected set of rendered-geometry segments, endpoints rounded
    and order-normalized so a route walked in reverse yields the same
    signature. Zero-length consecutive-duplicate pairs are skipped."""
    segments: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for a, b in zip(geometry, geometry[1:]):
        point_a = (round(a.lat, SEGMENT_COORD_PRECISION), round(a.lon, SEGMENT_COORD_PRECISION))
        point_b = (round(b.lat, SEGMENT_COORD_PRECISION), round(b.lon, SEGMENT_COORD_PRECISION))
        if point_a == point_b:
            continue
        segment = (point_a, point_b) if point_a <= point_b else (point_b, point_a)
        segments.add(segment)

    return frozenset(segments)


def segment_overlap(a: SegmentSignature, b: SegmentSignature) -> float:
    """Jaccard index |A n B| / |A u B| over two segment signatures."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def select_diverse(
    ranked: list[T],
    geometry_fn: Callable[[T], tuple[RoutePoint, ...]],
    count: int,
    max_overlap: float = DEFAULT_MAX_OVERLAP,
) -> list[T]:
    """Select up to `count` items from `ranked` (already sorted
    best-to-worst), preferring ones whose rendered geometry doesn't
    meaningfully overlap (`<= max_overlap` Jaccard) with an already-
    selected item. Falls back to filling remaining slots in original
    rank order once diversity can't be satisfied -- always returns
    `min(count, len(ranked))` items, never fewer."""
    if len(ranked) <= count:
        return list(ranked)

    selected_indices: list[int] = []
    selected_signatures: list[SegmentSignature] = []
    skipped_indices: list[int] = []

    for index, item in enumerate(ranked):
        if len(selected_indices) >= count:
            break
        signature = route_segment_signature(geometry_fn(item))
        if all(
            segment_overlap(signature, existing) <= max_overlap
            for existing in selected_signatures
        ):
            selected_indices.append(index)
            selected_signatures.append(signature)
        else:
            skipped_indices.append(index)

    if len(selected_indices) < count:
        for index in skipped_indices:
            if len(selected_indices) >= count:
                break
            selected_indices.append(index)

    # Preserve original rank order among the chosen subset -- diversity
    # only decides WHICH candidates are kept, never their display order.
    selected_indices.sort()
    return [ranked[index] for index in selected_indices]
