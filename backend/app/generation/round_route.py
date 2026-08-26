from typing import Any

from app.generation.reuse_penalty import reuse_penalized_return_path
from app.generation.shape_metrics import isoperimetric_quotient
from app.generation.turnarounds import select_turnarounds
from app.graph.distances import (
    nearest_node,
    outbound_path,
    single_source_distances,
)
from app.graph.model import path_to_candidate
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate


# Upper bound on how many turnarounds actually get a full return-leg
# Dijkstra (`reuse_penalized_return_path`) per call, independent of how
# large `count` grows for an overcomplete candidate pool. Without this,
# an overcomplete pool request (e.g. facility-free routing asking for a
# raw pool wider than the final result count) would multiply real Dijkstra
# searches by `count * 2` on every radius-scale tuning iteration -- the
# turnaround fix (see app.generation.turnarounds) means that multiplier
# is no longer throttled by accidental sector starvation, so it needs an
# explicit ceiling to keep latency bounded.
MAX_TURNAROUND_ATTEMPTS = 10

# Bounded number of alternate (smaller-radius, SAME-corridor) turnarounds
# tried per overshooting candidate -- see `_correct_overshoot`. Each
# attempt reruns the reuse-penalized return-leg Dijkstra, so this bounds
# the extra Dijkstra cost the correction can add per candidate.
MAX_OVERSHOOT_CORRECTION_ATTEMPTS = 3


def _shrink_candidates(
    outbound: list[int],
    start_node: int,
    dists: dict[int, float],
    turnaround_dist: float,
    target_distance_m: float,
    current_total_m: float,
) -> list[int]:
    """Nodes strictly before the turnaround on its OWN outbound path,
    ordered nearest-first to a proportionally shrunk target radius.

    A round loop's total distance is outbound + a reuse-penalised return
    leg, and that return leg's length can vary substantially between
    turnarounds even at the same nominal radius (street-topology
    dependent detours around already-used edges -- see
    `_correct_overshoot`). Assuming the return leg shrinks roughly
    proportionally to the outbound leg gives a cheap first estimate of
    which nearer-to-start node on the SAME corridor would land closest to
    `target_distance_m`; no new Dijkstra is needed here since every node
    on `outbound` already has a known cumulative distance in `dists`
    (it's a shortest-path prefix)."""
    if current_total_m <= 0:
        return []

    shrink_ratio = target_distance_m / current_total_m
    target_radius_m = turnaround_dist * shrink_ratio

    candidates = [node for node in outbound[:-1] if node != start_node]
    candidates.sort(key=lambda node: abs(dists.get(node, float("inf")) - target_radius_m))
    return candidates


def _correct_overshoot(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    paths: dict[int, list[int]] | None,
    outbound: list[int],
    turnaround: int,
    turnaround_dist: float,
    candidate: RouteCandidate,
    loop: list[int],
    target_distance_m: float,
    tolerance_m: float,
) -> tuple[RouteCandidate, list[int]]:
    """If `candidate` overshoots `target_distance_m` by more than
    `tolerance_m`, try up to `MAX_OVERSHOOT_CORRECTION_ATTEMPTS`
    alternate turnarounds on the SAME outbound corridor, closer to
    start -- keeping the loop's direction/topology but shrinking its
    radius. A spur can only ADD length (see `length_tune.tune_pair_to_target`),
    so an overshooting round candidate is otherwise returned unchanged no
    matter how many OTHER turnarounds the batch-level radius_scale search
    tried; those other turnarounds have their own, differently-detoured
    return legs and don't shrink THIS candidate.

    Every attempt's outbound leg is a free lookup into the already-cached
    single-source `paths` (no new Dijkstra); only the return leg
    (`reuse_penalized_return_path`) is recomputed, bounded by the attempt
    cap. Falls back to the original candidate if no correction lands
    within tolerance or improves on it."""
    error = candidate.distance_m - target_distance_m
    if error <= tolerance_m:
        return candidate, loop

    best_candidate, best_loop, best_error = candidate, loop, error

    shrink_candidates = _shrink_candidates(
        outbound, start_node, dists, turnaround_dist, target_distance_m, candidate.distance_m
    )

    for alt_turnaround in shrink_candidates[:MAX_OVERSHOOT_CORRECTION_ATTEMPTS]:
        alt_outbound = paths.get(alt_turnaround) if paths is not None else None
        if not alt_outbound:
            try:
                alt_outbound = outbound_path(graph, start_node, alt_turnaround, paths)
            except RouteNotFoundError:
                continue

        alt_return = reuse_penalized_return_path(graph, alt_turnaround, start_node, alt_outbound)
        if alt_return is None:
            continue

        alt_loop = alt_outbound + alt_return[1:]
        alt_candidate = path_to_candidate(graph, alt_loop)
        alt_error = alt_candidate.distance_m - target_distance_m

        if abs(alt_error) < abs(best_error):
            best_candidate, best_loop, best_error = alt_candidate, alt_loop, alt_error
        if abs(best_error) <= tolerance_m:
            break

    return best_candidate, best_loop


def round_pairs(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
    paths: dict[int, list[int]] | None = None,
    tolerance_m: float | None = None,
) -> list[tuple[RouteCandidate, list[int]]]:
    """(candidate, loop_node_path) pairs ranked roundest first.

    Shared by the public generator and the length tuner. For each
    bearing-diverse turnaround the outbound leg is the shortest path out
    and the return leg is a reuse-penalised Dijkstra preferring fresh
    streets. Ranked by isoperimetric quotient (roundest first).

    When `tolerance_m` is given, each candidate that overshoots the
    target by more than that tolerance gets a bounded per-candidate
    correction attempt (see `_correct_overshoot`) before scoring --
    round routes' return legs vary enough between turnarounds that a
    single batch-wide `radius_scale` (chosen to fit the closest
    candidate) can leave every OTHER candidate overshooting, and a spur
    can only fix undershoot.
    """
    target_radius_m = (target_distance_m / 2.0) * radius_scale
    turnarounds = select_turnarounds(
        graph,
        start_node,
        dists,
        target_radius_m,
        min(count * 2, MAX_TURNAROUND_ATTEMPTS),
        prefer_straight=False,
    )

    scored: list[tuple[float, RouteCandidate, list[int]]] = []

    for turnaround, turnaround_dist in turnarounds:
        try:
            outbound = outbound_path(graph, start_node, turnaround, paths)
        except RouteNotFoundError:
            continue

        return_path = reuse_penalized_return_path(
            graph, turnaround, start_node, outbound
        )
        if return_path is None:
            continue

        loop = outbound + return_path[1:]
        candidate = path_to_candidate(graph, loop)

        if tolerance_m is not None:
            candidate, loop = _correct_overshoot(
                graph, start_node, dists, paths, outbound, turnaround, turnaround_dist,
                candidate, loop, target_distance_m, tolerance_m,
            )

        quotient = isoperimetric_quotient(candidate.geometry)
        scored.append((quotient, candidate, loop))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [(candidate, loop) for _, candidate, loop in scored[:count]]


def generate_round(
    graph: Any,
    start: Coordinate,
    target_distance_m: float,
    count: int,
    radius_scale: float = 1.0,
) -> list[RouteCandidate]:
    """Loop routes: out to a turnaround, back a different way.

    Ranked roundest first by isoperimetric quotient. The turnaround
    radius is ~target/2 scaled by `radius_scale`.
    """
    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)
    pairs = round_pairs(
        graph, start_node, dists, target_distance_m, count, radius_scale
    )
    return [candidate for candidate, _ in pairs]
