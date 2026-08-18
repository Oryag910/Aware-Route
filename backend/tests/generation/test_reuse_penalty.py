import networkx as nx

from app.generation.reuse_penalty import (
    REUSE_PENALTY,
    _reuse_penalty_weight,
    reuse_penalized_return_path,
)


def test_reused_edge_cost_is_multiplied_by_penalty() -> None:
    weight = _reuse_penalty_weight({frozenset((1, 2))}, penalty=4.0)
    edge_dict = {0: {"length": 100.0}}

    assert weight(1, 2, edge_dict) == 400.0


def test_unused_edge_keeps_base_length() -> None:
    weight = _reuse_penalty_weight({frozenset((1, 2))}, penalty=4.0)
    edge_dict = {0: {"length": 100.0}}

    assert weight(2, 3, edge_dict) == 100.0


def test_parallel_edges_use_minimum_length() -> None:
    weight = _reuse_penalty_weight(set(), penalty=4.0)
    edge_dict = {0: {"length": 150.0}, 1: {"length": 100.0}}

    assert weight(5, 6, edge_dict) == 100.0


def test_parallel_edges_minimum_length_is_penalized_when_reused() -> None:
    weight = _reuse_penalty_weight({frozenset((5, 6))}, penalty=4.0)
    edge_dict = {0: {"length": 150.0}, 1: {"length": 100.0}}

    assert weight(5, 6, edge_dict) == 400.0


def _diamond_graph() -> nx.MultiDiGraph:
    """1 <-> 2 directly (length 100, the outbound edge), and via 3
    (60 + 60 = 120) as the only alternate. Directed both ways so the
    return-leg Dijkstra (which runs turnaround -> start) has a real
    choice to make."""
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, length=100.0)
    graph.add_edge(2, 1, key=0, length=100.0)
    graph.add_edge(1, 3, key=0, length=60.0)
    graph.add_edge(3, 1, key=0, length=60.0)
    graph.add_edge(2, 3, key=0, length=60.0)
    graph.add_edge(3, 2, key=0, length=60.0)
    return graph


def test_return_path_avoids_reused_edge_when_alternate_is_cheaper_penalized() -> None:  # noqa: E501
    graph = _diamond_graph()

    # Outbound 1 -> 2 direct (the edge that must look expensive on the
    # way back). At penalty=4.0 the direct return costs 100*4=400,
    # while the 120-long detour via node 3 is cheaper -- so it wins.
    path = reuse_penalized_return_path(
        graph, turnaround=2, start_node=1, outbound=[1, 2], penalty=4.0
    )

    assert path == [2, 3, 1]


def test_return_path_takes_reused_edge_when_penalty_is_one() -> None:
    graph = _diamond_graph()

    # At penalty=1.0 (no inflation) the direct 100-length return beats
    # the 120-length detour, so the reused edge is used after all.
    path = reuse_penalized_return_path(
        graph, turnaround=2, start_node=1, outbound=[1, 2], penalty=1.0
    )

    assert path == [2, 1]


def test_default_penalty_matches_module_constant() -> None:
    graph = _diamond_graph()

    default_path = reuse_penalized_return_path(
        graph, turnaround=2, start_node=1, outbound=[1, 2]
    )
    explicit_path = reuse_penalized_return_path(
        graph,
        turnaround=2,
        start_node=1,
        outbound=[1, 2],
        penalty=REUSE_PENALTY,
    )

    assert default_path == explicit_path


def test_no_path_returns_none() -> None:
    graph = nx.MultiDiGraph()
    graph.add_node(1)
    graph.add_node(2)
    # No edges at all -- turnaround and start are disconnected.

    path = reuse_penalized_return_path(
        graph, turnaround=2, start_node=1, outbound=[1, 2]
    )

    assert path is None
