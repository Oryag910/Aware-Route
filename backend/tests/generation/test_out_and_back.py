import networkx as nx

from app.generation.out_and_back import out_and_back_path, out_and_back_pairs
from app.graph.distances import single_source_distances
from app.graph.model import path_distance_m
from tests.generation.conftest import ORIGIN_NODE


def test_out_and_back_path_is_symmetric(star_graph: nx.MultiDiGraph) -> None:
    # Origin -> outer-north (id 102) is a two-hop spoke.
    path = out_and_back_path(star_graph, ORIGIN_NODE, 102)
    # There-and-back: [origin, inner, outer, inner, origin].
    assert path[0] == ORIGIN_NODE
    assert path[-1] == ORIGIN_NODE
    assert path == [ORIGIN_NODE, 101, 102, 101, ORIGIN_NODE]

    # Second half is the reverse of the first half.
    midpoint = len(path) // 2
    assert path[:midpoint] == list(reversed(path[midpoint + 1:]))


def test_out_and_back_distance_is_twice_one_way(
    star_graph: nx.MultiDiGraph,
) -> None:
    one_way = path_distance_m(star_graph, [ORIGIN_NODE, 101, 102])
    full = out_and_back_path(star_graph, ORIGIN_NODE, 102)
    assert path_distance_m(star_graph, full) == 2 * one_way


def test_pairs_target_2km_are_near_target(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = single_source_distances(star_graph, ORIGIN_NODE)
    # target 2000m -> radius 1000m -> outer ring turnarounds -> each
    # out-and-back is ~2000m.
    pairs = out_and_back_pairs(
        star_graph, ORIGIN_NODE, dists, target_distance_m=2000.0, count=4
    )
    assert pairs
    for candidate, _path in pairs:
        assert abs(candidate.distance_m - 2000.0) <= 1.0


def test_pairs_return_node_paths(star_graph: nx.MultiDiGraph) -> None:
    dists = single_source_distances(star_graph, ORIGIN_NODE)
    pairs = out_and_back_pairs(
        star_graph, ORIGIN_NODE, dists, target_distance_m=2000.0, count=4
    )
    for candidate, path in pairs:
        assert path[0] == ORIGIN_NODE
        assert path[-1] == ORIGIN_NODE
        assert path_distance_m(star_graph, path) == candidate.distance_m
