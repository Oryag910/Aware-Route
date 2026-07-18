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
    """Pick up to `count` bearing-diverse turnaround nodes.

    Candidates are nodes whose distance-from-start falls within
    +/-RADIUS_WINDOW of `target_radius_m`. They are bucketed into 8
    bearing sectors; within each sector the node whose distance is
    closest to the target radius wins. Sectors are then ordered by the
    straightness of their winner (highest first when `prefer_straight`,
    which suits out-and-backs; loops pass False to favour meandering
    returns) and up to `count` are returned as (node_id, dist) pairs.
    """
    start_coord = node_coordinate(graph, start_node)

    lower = target_radius_m * (1.0 - RADIUS_WINDOW)
    upper = target_radius_m * (1.0 + RADIUS_WINDOW)

    # Best (closest-to-target-radius) candidate per bearing sector.
    sector_best: dict[int, tuple[int, float, float]] = {}

    for node, dist in dists.items():
        if node == start_node or dist < lower or dist > upper:
            continue

        node_coord = node_coordinate(graph, node)
        sector = int(bearing_deg(start_coord, node_coord) // SECTOR_WIDTH_DEG)
        sector = min(sector, SECTOR_COUNT - 1)

        radius_gap = abs(dist - target_radius_m)
        current = sector_best.get(sector)

        if current is None or radius_gap < abs(current[1] - target_radius_m):
            straight = _straightness(start_coord, node_coord, dist)
            sector_best[sector] = (node, dist, straight)

    winners = list(sector_best.values())
    winners.sort(key=lambda entry: entry[2], reverse=prefer_straight)

    return [(node, dist) for node, dist, _ in winners[:count]]
