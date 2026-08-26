import networkx as nx
import pytest

from app.generation.round_route import round_pairs
from app.graph.distances import single_source_paths
from app.routing.geometry import destination_point
from app.routing.provider import Coordinate


ORIGIN_NODE = 0
ORIGIN = Coordinate(lat=40.75, lon=-73.98)

# A single due-north corridor 0 -> 1 -> 2 -> 3 -> 4 -> 5 (200m hops, so
# dists from start are 200/400/600/800/1000). Only nodes 3 and 5 get a
# direct fresh return edge back to start (never reusing an outbound
# edge, so `reuse_penalized_return_path` never detours through it) --
# their lengths are deliberately NOT proportional to outbound distance,
# the real-graph phenomenon `_correct_overshoot` targets: a
# reuse-penalised return leg's length is street-topology dependent, not
# a fixed multiple of the turnaround radius.
#
# node 5 (the only node `select_turnarounds` will pick for a 2000m
# target -- see the test) has a long return (1400m), overshooting a
# 2000m target by 400m (1000 + 1400 = 2400). Node 3, closer to start on
# the SAME corridor, has its own much shorter return (1350m): 600 + 1350
# = 1950, within 100m of the 2000m target.
_HOP_M = 200.0
_RETURN_M = {3: 1350.0, 5: 1400.0}


@pytest.fixture
def overshoot_corridor_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(ORIGIN_NODE, x=ORIGIN.lon, y=ORIGIN.lat)

    prev = ORIGIN_NODE
    for i in range(1, 6):
        point = destination_point(ORIGIN, 0.0, _HOP_M * i)
        graph.add_node(i, x=point.lon, y=point.lat)
        graph.add_edge(prev, i, key=0, length=_HOP_M)
        graph.add_edge(i, prev, key=0, length=_HOP_M)
        prev = i

    for node, return_m in _RETURN_M.items():
        graph.add_edge(node, ORIGIN_NODE, key=0, length=return_m)

    return graph


def test_round_pairs_without_tolerance_leaves_overshoot_uncorrected(
    overshoot_corridor_graph: nx.MultiDiGraph,
) -> None:
    """Default (no `tolerance_m`) behaviour is unchanged: node 5 is the
    only eligible turnaround for a 2000m target, and its overshooting
    total (1000 + 1400 = 2400m) is returned as-is."""
    dists, paths = single_source_paths(overshoot_corridor_graph, ORIGIN_NODE)
    pairs = round_pairs(
        overshoot_corridor_graph, ORIGIN_NODE, dists, target_distance_m=2000.0,
        count=1, paths=paths,
    )
    assert len(pairs) == 1
    candidate, _node_path = pairs[0]
    assert candidate.distance_m == pytest.approx(2400.0)


def test_round_pairs_with_tolerance_corrects_overshoot(
    overshoot_corridor_graph: nx.MultiDiGraph,
) -> None:
    """With `tolerance_m` set, the overshooting node-5 candidate is
    replaced by node 3 on the SAME corridor (600 + 1350 = 1950m, within
    100m of the 2000m target) -- preserving the loop's direction/
    topology while shrinking its radius, rather than being returned
    unchanged just because a spur can't shorten an overshoot."""
    dists, paths = single_source_paths(overshoot_corridor_graph, ORIGIN_NODE)
    pairs = round_pairs(
        overshoot_corridor_graph, ORIGIN_NODE, dists, target_distance_m=2000.0,
        count=1, paths=paths, tolerance_m=100.0,
    )
    assert len(pairs) == 1
    candidate, _node_path = pairs[0]
    assert abs(candidate.distance_m - 2000.0) <= 100.0
    assert candidate.distance_m == pytest.approx(1950.0)


def test_round_pairs_correction_never_worse_than_uncorrected(
    overshoot_corridor_graph: nx.MultiDiGraph,
) -> None:
    """The correction only ever REPLACES the original candidate with a
    strictly closer one (or leaves it alone) -- it must never land
    farther from target than the uncorrected candidate."""
    dists, paths = single_source_paths(overshoot_corridor_graph, ORIGIN_NODE)
    uncorrected, _ = round_pairs(
        overshoot_corridor_graph, ORIGIN_NODE, dists, target_distance_m=2000.0,
        count=1, paths=paths,
    )[0]
    corrected, _ = round_pairs(
        overshoot_corridor_graph, ORIGIN_NODE, dists, target_distance_m=2000.0,
        count=1, paths=paths, tolerance_m=100.0,
    )[0]
    assert abs(corrected.distance_m - 2000.0) <= abs(uncorrected.distance_m - 2000.0)
