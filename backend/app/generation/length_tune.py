from typing import Any, Callable

from app.generation.turnarounds import select_turnarounds
from app.graph.distances import shortest_path, single_source_distances
from app.graph.model import path_distance_m, path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import RouteCandidate


DEFAULT_TOLERANCE_M = 100.0

# Binary-search bounds on the generator's radius_scale, used to shrink
# an overshooting loop before the residual gets spurred up.
MIN_RADIUS_SCALE = 0.6
MAX_RADIUS_SCALE = 1.1
BINARY_SEARCH_STEPS = 5


def _spur_path(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    deficit_m: float,
) -> list[int] | None:
    """A short out-and-back node path from start absorbing ~`deficit_m`.

    Looks for a node at ~deficit/2 from the start (walked twice). If the
    closest candidate overshoots the deficit it retries at progressively
    smaller radii so the spur never blows past tolerance. Returns the
    out-and-back node path (start ... spur ... start) or None."""
    spur_radius = deficit_m / 2.0

    for scale in (1.0, 0.7, 0.5, 0.35, 0.25):
        turnarounds = select_turnarounds(
            graph,
            start_node,
            dists,
            spur_radius * scale,
            count=1,
            prefer_straight=True,
        )
        if not turnarounds:
            continue

        spur_target, _dist = turnarounds[0]
        try:
            spur = shortest_path(graph, start_node, spur_target)
        except RouteNotFoundError:
            continue

        spur_out_and_back = spur + list(reversed(spur))[1:]
        added = path_distance_m(graph, spur_out_and_back)

        # Accept when the spur does not overshoot the deficit by more
        # than tolerance; otherwise keep shrinking the radius.
        if added <= deficit_m + DEFAULT_TOLERANCE_M:
            return spur_out_and_back

    return None


def tune_pair_to_target(
    graph: Any,
    start_node: int,
    candidate: RouteCandidate,
    node_path: list[int],
    target_distance_m: float,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    dists: dict[int, float] | None = None,
) -> tuple[RouteCandidate, list[int]]:
    """Nudge a candidate's length toward the target with a spur, also
    returning the (possibly spur-spliced) node_path in sync with the
    tuned candidate. `tune_to_target` is a thin wrapper around this that
    drops the node_path for callers that only need the candidate.

    Within tolerance: returned unchanged. Too short: an out-and-back
    spur from the start is spliced in front of the route to absorb the
    deficit. (Overshoot is handled upstream by regenerating at a smaller
    radius via `tune_generator_to_target`.)
    """
    error = candidate.distance_m - target_distance_m
    if abs(error) <= tolerance_m:
        return candidate, node_path

    if error > 0:
        # Too long: nothing a spur can fix (it only adds length).
        return candidate, node_path

    deficit_m = -error
    if dists is None:
        dists = single_source_distances(graph, start_node)

    spur = _spur_path(graph, start_node, dists, deficit_m)
    if spur is None:
        return candidate, node_path

    # Spur out-and-back from the start, then the original route.
    tuned_path = spur + node_path[1:]
    return path_to_candidate(graph, tuned_path), tuned_path


def tune_to_target(
    graph: Any,
    start_node: int,
    candidate: RouteCandidate,
    node_path: list[int],
    target_distance_m: float,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    dists: dict[int, float] | None = None,
) -> RouteCandidate:
    """Nudge a candidate's length toward the target with a spur.

    Within tolerance: returned unchanged. Too short: an out-and-back
    spur from the start is spliced in front of the route to absorb the
    deficit. (Overshoot is handled upstream by regenerating at a smaller
    radius via `tune_generator_to_target`.)
    """
    tuned, _node_path = tune_pair_to_target(
        graph, start_node, candidate, node_path, target_distance_m, tolerance_m, dists
    )
    return tuned


def tune_generator_pairs_to_target(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    generate: Callable[[float], list[tuple[RouteCandidate, list[int]]]],
    target_distance_m: float,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
) -> list[tuple[RouteCandidate, list[int]]]:
    """Drive a radius-scaled generator to the target length, keeping
    each tuned candidate's node_path alongside it. `tune_generator_to_target`
    is a thin wrapper around this that drops the node_paths for callers
    that only need candidates.

    `generate(radius_scale)` returns (candidate, node_path) pairs. When
    the best candidate at full radius overshoots target+tolerance, a
    binary search on radius_scale (MIN..MAX) brackets down until it fits;
    the residual undershoot is then spurred up per candidate. Returns
    the tuned (candidate, node_path) pairs.
    """

    def best_distance(pairs: list[tuple[RouteCandidate, list[int]]]) -> float:
        if not pairs:
            return float("inf")
        return min(candidate.distance_m for candidate, _ in pairs)

    pairs = generate(1.0)
    chosen_scale = 1.0

    if best_distance(pairs) > target_distance_m + tolerance_m:
        low, high = MIN_RADIUS_SCALE, MAX_RADIUS_SCALE
        best_pairs = pairs
        chosen_scale = high

        for _ in range(BINARY_SEARCH_STEPS):
            mid = (low + high) / 2.0
            mid_pairs = generate(mid)
            distance = best_distance(mid_pairs)

            if distance <= target_distance_m + tolerance_m:
                # Fits: remember it, try a larger radius to close the gap.
                best_pairs = mid_pairs
                chosen_scale = mid
                low = mid
            else:
                high = mid

        pairs = best_pairs

    _ = chosen_scale  # documents which radius produced the kept pool
    return [
        tune_pair_to_target(
            graph,
            start_node,
            candidate,
            node_path,
            target_distance_m,
            tolerance_m,
            dists,
        )
        for candidate, node_path in pairs
    ]


def tune_generator_to_target(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    generate: Callable[[float], list[tuple[RouteCandidate, list[int]]]],
    target_distance_m: float,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
) -> list[RouteCandidate]:
    """Drive a radius-scaled generator to the target length.

    `generate(radius_scale)` returns (candidate, node_path) pairs. When
    the best candidate at full radius overshoots target+tolerance, a
    binary search on radius_scale (MIN..MAX) brackets down until it fits;
    the residual undershoot is then spurred up per candidate. Returns
    the tuned candidates.
    """
    pairs = tune_generator_pairs_to_target(
        graph, start_node, dists, generate, target_distance_m, tolerance_m
    )
    return [candidate for candidate, _node_path in pairs]
