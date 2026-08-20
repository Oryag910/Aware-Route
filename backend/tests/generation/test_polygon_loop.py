import networkx as nx
import pytest

from app.generation.polygon_loop import (
    MAX_EDGE_REUSE_RATIO,
    _NodeIndex,
    _build_loop,
    polygon_loop_pairs,
)
from app.generation.polygon_template import LoopTemplate
from app.generation.quality import edge_reuse_ratio
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.model import node_coordinate
from app.routing.geometry import destination_point, haversine_m
from app.routing.provider import Coordinate
from scripts.benchmark_suite import _short_start_return_spur


# A dense 41x41 grid (50m spacing, so a ~2000m square) centered on
# GRID_ORIGIN -- dense enough that reuse-penalized legs have real
# alternate streets, and large enough to comfortably contain every
# TEMPLATES rectangle at TARGET_DISTANCE_M with margin to spare.
GRID_N = 41
GRID_SPACING_M = 50.0
GRID_ORIGIN = Coordinate(lat=40.750, lon=-73.980)
GRID_CENTER_INDEX = GRID_N // 2

TARGET_DISTANCE_M = 2400.0
COUNT = 5


def _node_id(i: int, j: int) -> int:
    return i * GRID_N + j


def _node_coord(i: int, j: int) -> Coordinate:
    north = destination_point(GRID_ORIGIN, 0.0, i * GRID_SPACING_M)
    return destination_point(north, 90.0, j * GRID_SPACING_M)


@pytest.fixture(scope="module")
def grid_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(crs="epsg:4326")

    for i in range(GRID_N):
        for j in range(GRID_N):
            coord = _node_coord(i, j)
            graph.add_node(_node_id(i, j), x=coord.lon, y=coord.lat)

    for i in range(GRID_N):
        for j in range(GRID_N):
            u = _node_id(i, j)
            u_coord = _node_coord(i, j)
            if i + 1 < GRID_N:
                v = _node_id(i + 1, j)
                length = haversine_m(u_coord, _node_coord(i + 1, j))
                graph.add_edge(u, v, key=0, length=length)
                graph.add_edge(v, u, key=0, length=length)
            if j + 1 < GRID_N:
                v = _node_id(i, j + 1)
                length = haversine_m(u_coord, _node_coord(i, j + 1))
                graph.add_edge(u, v, key=0, length=length)
                graph.add_edge(v, u, key=0, length=length)

    return graph


@pytest.fixture(scope="module")
def start_node() -> int:
    return _node_id(GRID_CENTER_INDEX, GRID_CENTER_INDEX)


# ---------------------------------------------------------------------------
# polygon_loop_pairs on a realistic grid
# ---------------------------------------------------------------------------


def test_grid_produces_at_least_one_candidate(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert len(pairs) >= 1


def test_square_ish_loop_is_reasonably_compact(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    best_candidate, _best_path = pairs[0]  # ranked roundest first
    assert isoperimetric_quotient(best_candidate.geometry) > 0.3


def test_orientation_and_aspect_variation_produce_distinct_candidates(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert len(pairs) >= 2

    node_paths = [tuple(path) for _candidate, path in pairs]
    assert len(set(node_paths)) == len(node_paths)


def test_all_candidates_close_back_at_start(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    for _candidate, node_path in pairs:
        assert node_path[0] == start_node
        assert node_path[-1] == start_node


def test_node_path_is_continuously_connected(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    for _candidate, node_path in pairs:
        for u, v in zip(node_path, node_path[1:]):
            assert grid_graph.has_edge(u, v)


def test_low_edge_reuse_on_grid(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    for _candidate, node_path in pairs:
        assert edge_reuse_ratio(node_path) <= MAX_EDGE_REUSE_RATIO


def test_no_short_start_return_spur(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # V2 never splices a length_tune.py-style spur -- confirmed here with
    # the SAME detector PR #14 built for measuring exactly this artifact.
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    for candidate, _node_path in pairs:
        assert _short_start_return_spur(candidate.geometry) is False


def test_deterministic_across_calls(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    first = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    second = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)

    assert [path for _candidate, path in first] == [path for _candidate, path in second]


def test_best_candidate_within_generous_tolerance(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    pairs = polygon_loop_pairs(grid_graph, start_node, TARGET_DISTANCE_M, COUNT)
    assert pairs

    best_error = min(abs(candidate.distance_m - TARGET_DISTANCE_M) for candidate, _ in pairs)
    # Looser than production's DEFAULT_TOLERANCE_M (100m): this grid's
    # 50m edges make path length chunky, so exact-to-100m fits aren't
    # guaranteed on synthetic topology the way they are on the dense
    # real street graph.
    assert best_error <= 250.0


# ---------------------------------------------------------------------------
# graceful degradation / rejection guards
# ---------------------------------------------------------------------------


def test_target_distance_too_large_for_grid_degrades_gracefully(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # Grid extent is ~2000m across; asking for a route far bigger than
    # the graph can support must not raise -- it may just return fewer
    # (possibly zero) candidates.
    pairs = polygon_loop_pairs(grid_graph, start_node, 50_000.0, COUNT)
    assert isinstance(pairs, list)
    assert len(pairs) <= COUNT


def test_start_near_grid_corner_does_not_crash(grid_graph: nx.MultiDiGraph) -> None:
    corner_start = _node_id(2, 2)
    pairs = polygon_loop_pairs(grid_graph, corner_start, TARGET_DISTANCE_M, COUNT)
    assert isinstance(pairs, list)
    assert len(pairs) <= COUNT


def test_build_loop_rejects_anchor_collapsing_onto_start(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # A tiny target distance places B ~10m from start -- well inside a
    # single 50m grid cell, so it snaps to the start node itself.
    template = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")
    start_coord = node_coordinate(grid_graph, start_node)

    result = _build_loop(
        grid_graph, start_node, _NodeIndex(grid_graph), template, start_coord,
        target_distance_m=40.0, scale=1.0, paths=None,
    )

    assert result is None


def test_build_loop_returns_none_when_topology_too_sparse_for_four_anchors() -> None:
    # Only two nodes exist at all -- any synthetic B/C/D anchor can only
    # ever snap onto start or the one other node, so a 4-anchor loop is
    # topologically impossible here.
    graph = nx.MultiDiGraph(crs="epsg:4326")
    start = Coordinate(lat=40.750, lon=-73.980)
    graph.add_node(1, x=start.lon, y=start.lat)

    near = destination_point(start, 0.0, 30.0)
    graph.add_node(2, x=near.lon, y=near.lat)
    graph.add_edge(1, 2, key=0, length=30.0)
    graph.add_edge(2, 1, key=0, length=30.0)

    template = LoopTemplate(rotation_deg=0.0, aspect_ratio=1.0, direction="cw")
    result = _build_loop(
        graph, 1, _NodeIndex(graph), template, start, target_distance_m=2000.0,
        scale=1.0, paths=None,
    )

    assert result is None


def test_zero_or_negative_target_distance_returns_empty(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    assert polygon_loop_pairs(grid_graph, start_node, 0.0, COUNT) == []
    assert polygon_loop_pairs(grid_graph, start_node, -100.0, COUNT) == []
