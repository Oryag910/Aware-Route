from math import isclose

import networkx as nx
import pytest

from app.generation.quality import (
    corrective_loop_penalty,
    edge_reuse_ratio,
    elevation_gain_from_nodes,
    pedestrian_share,
    waytype_breakdown,
)


# Node IDs mirror real OSM node IDs (ints), consistent with
# tests/graph/conftest.py.
NODE_A = 1
NODE_B = 2
NODE_C = 3
NODE_D = 4


@pytest.fixture
def loop_graph() -> nx.MultiDiGraph:
    """A -> B -> C -> D -> A square loop, each edge length 100m.

    A -> B and B -> C are footway; C -> D and D -> A are residential.
    Every edge has a reverse counterpart so the loop can be walked
    A -> B -> C -> D -> A without any directed-edge reuse.
    """
    graph = nx.MultiDiGraph(crs="epsg:4326")

    graph.add_node(NODE_A, x=0.0, y=0.0)
    graph.add_node(NODE_B, x=0.001, y=0.0)
    graph.add_node(NODE_C, x=0.001, y=0.001)
    graph.add_node(NODE_D, x=0.0, y=0.001)

    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0, highway="footway")
    graph.add_edge(NODE_B, NODE_A, key=0, length=100.0, highway="footway")

    graph.add_edge(NODE_B, NODE_C, key=0, length=100.0, highway="footway")
    graph.add_edge(NODE_C, NODE_B, key=0, length=100.0, highway="footway")

    graph.add_edge(NODE_C, NODE_D, key=0, length=100.0, highway="residential")
    graph.add_edge(NODE_D, NODE_C, key=0, length=100.0, highway="residential")

    graph.add_edge(NODE_D, NODE_A, key=0, length=100.0, highway="residential")
    graph.add_edge(NODE_A, NODE_D, key=0, length=100.0, highway="residential")

    return graph


@pytest.fixture
def spur_graph() -> nx.MultiDiGraph:
    """A -> B -> C path (out-and-back capable), all footway, 100m each."""
    graph = nx.MultiDiGraph(crs="epsg:4326")

    graph.add_node(NODE_A, x=0.0, y=0.0)
    graph.add_node(NODE_B, x=0.001, y=0.0)
    graph.add_node(NODE_C, x=0.002, y=0.0)

    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0, highway="footway")
    graph.add_edge(NODE_B, NODE_A, key=0, length=100.0, highway="footway")
    graph.add_edge(NODE_B, NODE_C, key=0, length=100.0, highway="footway")
    graph.add_edge(NODE_C, NODE_B, key=0, length=100.0, highway="footway")

    return graph


# -- edge_reuse_ratio ---------------------------------------------------


def test_edge_reuse_ratio_empty_and_single_node_path() -> None:
    assert edge_reuse_ratio([]) == 0.0
    assert edge_reuse_ratio([NODE_A]) == 0.0


def test_edge_reuse_ratio_pure_out_and_back_is_high() -> None:
    # A -> B -> C -> B -> A: 4 hops, the last two both revisit the first
    # two's undirected edges.
    path = [NODE_A, NODE_B, NODE_C, NODE_B, NODE_A]
    assert isclose(edge_reuse_ratio(path), 0.5)


def test_edge_reuse_ratio_full_retrace_is_near_one() -> None:
    # A -> B -> A -> B -> A: every hop after the first reuses an edge.
    path = [NODE_A, NODE_B, NODE_A, NODE_B, NODE_A]
    assert isclose(edge_reuse_ratio(path), 0.75)


def test_edge_reuse_ratio_mixed_loop_is_zero() -> None:
    # A -> B -> C -> D -> A: a closed loop with no repeated undirected
    # edge.
    path = [NODE_A, NODE_B, NODE_C, NODE_D, NODE_A]
    assert edge_reuse_ratio(path) == 0.0


# -- waytype_breakdown ---------------------------------------------------


def test_waytype_breakdown_empty_and_single_node_path(
    loop_graph: nx.MultiDiGraph,
) -> None:
    assert waytype_breakdown(loop_graph, []) == {}
    assert waytype_breakdown(loop_graph, [NODE_A]) == {}


def test_waytype_breakdown_sums_to_one(loop_graph: nx.MultiDiGraph) -> None:
    path = [NODE_A, NODE_B, NODE_C, NODE_D, NODE_A]
    breakdown = waytype_breakdown(loop_graph, path)

    assert isclose(sum(breakdown.values()), 1.0)
    assert isclose(breakdown["footway"], 0.5)
    assert isclose(breakdown["residential"], 0.5)


def test_waytype_breakdown_single_class(spur_graph: nx.MultiDiGraph) -> None:
    path = [NODE_A, NODE_B, NODE_C]
    breakdown = waytype_breakdown(spur_graph, path)

    assert breakdown == {"footway": 1.0}


# -- pedestrian_share ------------------------------------------------------


def test_pedestrian_share_empty_path(loop_graph: nx.MultiDiGraph) -> None:
    assert pedestrian_share(loop_graph, []) == 0.0


def test_pedestrian_share_footway_heavy_path_is_high(
    spur_graph: nx.MultiDiGraph,
) -> None:
    path = [NODE_A, NODE_B, NODE_C]
    assert pedestrian_share(spur_graph, path) == 1.0


def test_pedestrian_share_half_footway_half_residential(
    loop_graph: nx.MultiDiGraph,
) -> None:
    path = [NODE_A, NODE_B, NODE_C, NODE_D, NODE_A]
    assert isclose(pedestrian_share(loop_graph, path), 0.5)


def test_pedestrian_share_all_vehicular_is_zero() -> None:
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(NODE_A, x=0.0, y=0.0)
    graph.add_node(NODE_B, x=0.001, y=0.0)
    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0, highway="primary")

    assert pedestrian_share(graph, [NODE_A, NODE_B]) == 0.0


# -- elevation_gain_from_nodes ----------------------------------------------


def test_elevation_gain_empty_and_single_node_path(
    loop_graph: nx.MultiDiGraph,
) -> None:
    assert elevation_gain_from_nodes(loop_graph, []) == 0.0
    assert elevation_gain_from_nodes(loop_graph, [NODE_A]) == 0.0


def test_elevation_gain_returns_none_when_any_node_lacks_elevation() -> None:
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(NODE_A, x=0.0, y=0.0, elevation=10.0)
    graph.add_node(NODE_B, x=0.001, y=0.0)  # no elevation attribute.
    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0)

    assert elevation_gain_from_nodes(graph, [NODE_A, NODE_B]) is None


def test_elevation_gain_sums_only_positive_deltas() -> None:
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(NODE_A, x=0.0, y=0.0, elevation=10.0)
    graph.add_node(NODE_B, x=0.001, y=0.0, elevation=25.0)
    graph.add_node(NODE_C, x=0.002, y=0.0, elevation=5.0)
    graph.add_node(NODE_D, x=0.003, y=0.0, elevation=15.0)
    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0)
    graph.add_edge(NODE_B, NODE_C, key=0, length=100.0)
    graph.add_edge(NODE_C, NODE_D, key=0, length=100.0)

    path = [NODE_A, NODE_B, NODE_C, NODE_D]
    # +15 (A->B), -20 ignored (B->C), +10 (C->D) = 25.
    assert isclose(elevation_gain_from_nodes(graph, path) or 0.0, 25.0)


# -- corrective_loop_penalty -------------------------------------------------


def test_corrective_loop_penalty_short_paths_are_zero() -> None:
    assert corrective_loop_penalty([]) == 0.0
    assert corrective_loop_penalty([NODE_A]) == 0.0
    assert corrective_loop_penalty([NODE_A, NODE_B]) == 0.0


def test_corrective_loop_penalty_clean_loop_is_zero() -> None:
    # No node repeats at all, so no there-and-back window exists.
    path = [NODE_A, NODE_B, NODE_C, NODE_D, NODE_A]
    assert corrective_loop_penalty(path) == 0.0


def test_corrective_loop_penalty_immediate_backtrack_is_full() -> None:
    # A -> B -> A: the entire (only) window is a depth-1 stub.
    path = [NODE_A, NODE_B, NODE_A]
    assert corrective_loop_penalty(path) == 1.0


def test_corrective_loop_penalty_short_out_and_back_flagged_at_default_depth() -> None:
    # At small scale (turnaround only 2 hops out), a there-and-back
    # reads the same as a corrective stub -- that's intentional (see
    # docstring): the metric can't distinguish shape intent from
    # node_path alone.
    path = [NODE_A, NODE_B, NODE_C, NODE_B, NODE_A]
    assert corrective_loop_penalty(path, max_stub_depth=2) > 0.0


def test_corrective_loop_penalty_long_out_and_back_scores_lower_than_short_one() -> None:
    # A long out-and-back's turnaround still forms one small flagged
    # window (the hop pair immediately either side of the turnaround
    # node), but it's diluted across many more total hops than a short
    # out-and-back of the same shape -- so the long one scores lower.
    node_e, node_f, node_g = 5, 6, 7
    short_out_and_back = [NODE_A, NODE_B, NODE_C, NODE_B, NODE_A]
    long_out_and_back = [
        NODE_A,
        NODE_B,
        NODE_C,
        NODE_D,
        node_e,
        node_f,
        node_g,
        node_f,
        node_e,
        NODE_D,
        NODE_C,
        NODE_B,
        NODE_A,
    ]

    short_penalty = corrective_loop_penalty(short_out_and_back, max_stub_depth=2)
    long_penalty = corrective_loop_penalty(long_out_and_back, max_stub_depth=2)

    assert long_penalty < short_penalty


def test_corrective_loop_penalty_detects_appended_stub() -> None:
    # A -> B -> C -> D -> A is a clean loop; then a spur B -> C -> B is
    # appended (immediate backtrack) for length tuning.
    path = [NODE_A, NODE_B, NODE_C, NODE_D, NODE_A, NODE_B, NODE_C, NODE_B]
    penalty = corrective_loop_penalty(path, max_stub_depth=2)

    assert penalty > 0.0
    # 7 total hops; the appended stub's C->B->C... window: positions
    # 5,6,7 are B,C,B (depth 1, distinct interior) -> flags hops 5,6.
    assert isclose(penalty, 2 / 7)
