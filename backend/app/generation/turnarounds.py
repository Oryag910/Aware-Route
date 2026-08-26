from collections import defaultdict
from typing import Any

from app.graph.model import node_coordinate
from app.routing.geometry import bearing_deg, haversine_m
from app.routing.provider import Coordinate


# Candidate turnaround nodes must sit within this fractional band of the
# requested radius so tuned routes start close to the target length.
RADIUS_WINDOW = 0.25

# 8 bearing sectors of 45 degrees each, so returned turnarounds spread
# around the compass rather than clustering in one direction.
SECTOR_COUNT = 8
SECTOR_WIDTH_DEG = 360.0 / SECTOR_COUNT

# A fallback (second-or-later) pick from an already-populated sector must
# sit at least this far from every already-chosen turnaround, so it can't
# just be an adjacent node on the same stretch of street -- which would
# produce a near-identical route under a different node id. Comfortably
# above ordinary Manhattan block spacing (~80m) so two picks are never on
# the same block, comfortably below the narrowest realistic radius window
# so short routes still have room for a fallback pick.
MIN_TURNAROUND_SEPARATION_M = 250.0

# Bounded width of the fallback scan: once the first-pass (one-per-sector)
# winners are short of `count`, at most this many additional candidates
# (pooled round-robin across populated sectors, closest-to-target-radius
# first) are inspected for a separation-qualifying pick. Keeps selection
# cheap and deterministic even when one sector holds thousands of eligible
# nodes (e.g. a long out-and-back near a peninsula tip).
FALLBACK_SCAN_BUDGET = 60


def _straightness(start: Coordinate, node_coord: Coordinate, dist: float) -> float:
    """As-the-crow-flies distance over graph distance. 1.0 == a straight
    shot from the start; lower means the shortest path meanders."""
    if dist == 0.0:
        return 0.0
    return haversine_m(start, node_coord) / dist


def select_turnarounds(
    graph: Any,
    start_node: int,
    dists: dict[int, float],
    target_radius_m: float,
    count: int,
    prefer_straight: bool = True,
) -> list[tuple[int, float]]:
    """Pick up to `count` turnaround nodes, favouring directional
    diversity first and topping up from populated sectors if that isn't
    enough to fill the budget.

    Candidates are nodes whose distance-from-start falls within
    +/-RADIUS_WINDOW of `target_radius_m`, bucketed into 8 bearing
    sectors ordered by distance-to-target-radius within each bucket.

    Pass 1 takes the single best (closest-to-target-radius) candidate
    from every populated sector -- identical to the original
    one-per-sector behaviour, so routes stay spread around the compass
    whenever enough sectors are populated.

    Pass 2 only runs if pass 1 came up short of `count` (Manhattan can
    legitimately place thousands of eligible nodes in one narrow bearing
    sector near a peninsula tip or graph boundary, starving every other
    sector). It tops up from the same populated sectors' leftover
    candidates, round-robin and closest-to-target-radius first, skipping
    anything within MIN_TURNAROUND_SEPARATION_M of an already-chosen
    turnaround so a fallback pick can't just be an adjacent node on the
    same street producing a near-duplicate route. Bounded by
    FALLBACK_SCAN_BUDGET.

    Sectors are then ordered by the straightness of their winner (highest
    first when `prefer_straight`, which suits out-and-backs; loops pass
    False to favour meandering returns) and up to `count` are returned as
    (node_id, dist) pairs.
    """
    start_coord = node_coordinate(graph, start_node)

    lower = target_radius_m * (1.0 - RADIUS_WINDOW)
    upper = target_radius_m * (1.0 + RADIUS_WINDOW)

    # Eligible nodes bucketed by bearing sector, each list sorted
    # closest-to-target-radius first (ties broken by node id for
    # determinism).
    by_sector: dict[int, list[tuple[int, float, float, Coordinate]]] = defaultdict(list)

    for node, dist in dists.items():
        if node == start_node or dist < lower or dist > upper:
            continue

        node_coord = node_coordinate(graph, node)
        sector = int(bearing_deg(start_coord, node_coord) // SECTOR_WIDTH_DEG)
        sector = min(sector, SECTOR_COUNT - 1)
        straight = _straightness(start_coord, node_coord, dist)
        by_sector[sector].append((node, dist, straight, node_coord))

    for sector, candidates in by_sector.items():
        candidates.sort(key=lambda entry: (abs(entry[1] - target_radius_m), entry[0]))

    # Pass 1: one winner per populated sector, preserving directional
    # diversity exactly as before.
    chosen: list[tuple[int, float, float]] = []
    chosen_coords: list[Coordinate] = []
    leftovers: dict[int, list[tuple[int, float, float, Coordinate]]] = {}

    for sector in sorted(by_sector):
        node, dist, straight, node_coord = by_sector[sector][0]
        chosen.append((node, dist, straight))
        chosen_coords.append(node_coord)
        leftovers[sector] = by_sector[sector][1:]

    # Pass 2: bounded, deterministic fallback fill from already-populated
    # sectors when too few sectors were populated to reach `count`.
    if len(chosen) < count and any(leftovers.values()):
        sector_order = sorted(leftovers)
        queue: list[tuple[int, float, float, Coordinate]] = []
        row = 0
        while len(queue) < FALLBACK_SCAN_BUDGET and any(
            row < len(leftovers[sector]) for sector in sector_order
        ):
            for sector in sector_order:
                if row < len(leftovers[sector]):
                    queue.append(leftovers[sector][row])
            row += 1

        for node, dist, straight, node_coord in queue[:FALLBACK_SCAN_BUDGET]:
            if len(chosen) >= count:
                break
            if all(
                haversine_m(node_coord, other) >= MIN_TURNAROUND_SEPARATION_M
                for other in chosen_coords
            ):
                chosen.append((node, dist, straight))
                chosen_coords.append(node_coord)

    chosen.sort(key=lambda entry: entry[2], reverse=prefer_straight)

    return [(node, dist) for node, dist, _ in chosen[:count]]
