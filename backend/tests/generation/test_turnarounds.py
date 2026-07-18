import networkx as nx

from app.generation.turnarounds import select_turnarounds
from app.graph.distances import single_source_distances
from app.routing.geometry import bearing_deg
from app.routing.provider import Coordinate
from tests.generation.conftest import ORIGIN, ORIGIN_NODE


def _dists(star_graph: nx.MultiDiGraph) -> dict[int, float]:
    return single_source_distances(star_graph, ORIGIN_NODE)


def test_selects_nodes_within_radius_window(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    # Target 1000m: only the outer ring (1000m) is within +/-25%; the
    # inner ring (500m) is well outside and must be excluded.
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    picked_dists = [dist for _, dist in picked]

    assert picked_dists
    assert all(750.0 <= dist <= 1250.0 for dist in picked_dists)


def test_excludes_inner_ring_at_outer_target(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    inner_ids = {101, 111, 121, 131}
    assert not (set(node for node, _ in picked) & inner_ids)


def test_bearing_diversity_one_per_sector(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    # The four outer spokes point N/E/S/W -- four distinct 45deg sectors,
    # so all four should be returned (never two from the same direction).
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    assert len(picked) == 4

    bearings = [
        round(
            bearing_deg(ORIGIN, _coord(star_graph, node)) / 45.0
        )
        for node, _ in picked
    ]
    assert len(set(b % 8 for b in bearings)) == 4


def test_count_caps_result(star_graph: nx.MultiDiGraph) -> None:
    dists = _dists(star_graph)
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=2
    )
    assert len(picked) == 2


def _coord(graph: nx.MultiDiGraph, node: int) -> Coordinate:
    data = graph.nodes[node]
    return Coordinate(lat=data["y"], lon=data["x"])
