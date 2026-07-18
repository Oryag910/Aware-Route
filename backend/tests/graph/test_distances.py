import networkx as nx
import pytest

from app.graph.distances import (
    nearest_node,
    shortest_path,
    single_source_distances,
)
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate
from tests.graph.conftest import NODE_A, NODE_B, NODE_C, NODE_D, NODE_E, NODE_F


def test_nearest_node_snaps_to_closest_coordinate(
    small_graph: nx.MultiDiGraph,
) -> None:
    # Very close to B (0.001, 0.0).
    node = nearest_node(small_graph, Coordinate(lat=0.0001, lon=0.0011))

    assert node == NODE_B


def test_nearest_node_matches_exact_node_coordinate(
    small_graph: nx.MultiDiGraph,
) -> None:
    node = nearest_node(small_graph, Coordinate(lat=0.001, lon=0.001))

    assert node == NODE_D


def test_shortest_path_uses_length_weight(
    small_graph: nx.MultiDiGraph,
) -> None:
    path = shortest_path(small_graph, NODE_A, NODE_D)

    assert path == [NODE_A, NODE_B, NODE_D]


def test_shortest_path_raises_route_not_found_for_disconnected_node(
    small_graph: nx.MultiDiGraph,
) -> None:
    with pytest.raises(RouteNotFoundError):
        shortest_path(small_graph, NODE_A, NODE_F)


def test_single_source_distances_computes_dijkstra_from_source(
    small_graph: nx.MultiDiGraph,
) -> None:
    distances = single_source_distances(small_graph, NODE_A)

    assert distances[NODE_A] == pytest.approx(0.0)
    assert distances[NODE_B] == pytest.approx(100.0)
    assert distances[NODE_D] == pytest.approx(220.0)
    assert distances[NODE_E] == pytest.approx(320.0)
    # C reached via the shorter of the two parallel B -> C edges.
    assert distances[NODE_C] == pytest.approx(200.0)
    # F is disconnected -- absent from the result.
    assert NODE_F not in distances
