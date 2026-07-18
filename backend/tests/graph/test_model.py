import networkx as nx
import pytest

from app.graph.model import (
    node_coordinate,
    path_distance_m,
    path_to_candidate,
    path_to_geometry,
)
from app.routing.provider import Coordinate
from tests.graph.conftest import NODE_A, NODE_B, NODE_C, NODE_D


def test_node_coordinate_reads_y_as_lat_and_x_as_lon(
    small_graph: nx.MultiDiGraph,
) -> None:
    assert node_coordinate(small_graph, NODE_B) == Coordinate(
        lat=0.0, lon=0.001
    )


def test_path_distance_m_picks_min_parallel_edge_length(
    small_graph: nx.MultiDiGraph,
) -> None:
    # B -> C has two parallel edges: 150.0 and 100.0. The minimum (100.0)
    # must be used.
    distance = path_distance_m(small_graph, [NODE_B, NODE_C])

    assert distance == pytest.approx(100.0)


def test_path_distance_m_sums_across_multiple_hops(
    small_graph: nx.MultiDiGraph,
) -> None:
    # A -> B (100.0) + B -> D (120.0) = 220.0
    distance = path_distance_m(small_graph, [NODE_A, NODE_B, NODE_D])

    assert distance == pytest.approx(220.0)


def test_path_to_geometry_straight_line_fallback(
    small_graph: nx.MultiDiGraph,
) -> None:
    geometry = path_to_geometry(small_graph, [NODE_A, NODE_B])

    assert len(geometry) == 2
    assert geometry[0].lat == pytest.approx(0.0)
    assert geometry[0].lon == pytest.approx(0.0)
    assert geometry[1].lat == pytest.approx(0.0)
    assert geometry[1].lon == pytest.approx(0.001)
    assert all(point.elevation_m == 0.0 for point in geometry)


def test_path_to_geometry_uses_edge_geometry_and_reorients_it(
    small_graph: nx.MultiDiGraph,
) -> None:
    # B -> D stores its LineString reversed (D -> B order). Traversing
    # B -> D must re-orient it so the first point matches B, not D.
    geometry = path_to_geometry(small_graph, [NODE_B, NODE_D])

    assert len(geometry) == 3
    # First point matches B's coordinate.
    assert geometry[0].lon == pytest.approx(0.001)
    assert geometry[0].lat == pytest.approx(0.0)
    # Middle point is the bowed-out midpoint from the stored geometry.
    assert geometry[1].lon == pytest.approx(0.0015)
    assert geometry[1].lat == pytest.approx(0.0005)
    # Last point matches D's coordinate.
    assert geometry[2].lon == pytest.approx(0.001)
    assert geometry[2].lat == pytest.approx(0.001)


def test_path_to_geometry_drops_duplicated_shared_vertices(
    small_graph: nx.MultiDiGraph,
) -> None:
    # A -> B -> D: A -> B contributes 2 points, B -> D contributes 3
    # points but shares its first point (B) with A -> B's last point.
    # Expected total: 2 + 3 - 1 = 4 points, not 5.
    geometry = path_to_geometry(small_graph, [NODE_A, NODE_B, NODE_D])

    assert len(geometry) == 4
    assert geometry[0].lon == pytest.approx(0.0)
    assert geometry[0].lat == pytest.approx(0.0)
    assert geometry[1].lon == pytest.approx(0.001)
    assert geometry[1].lat == pytest.approx(0.0)
    assert geometry[2].lon == pytest.approx(0.0015)
    assert geometry[2].lat == pytest.approx(0.0005)
    assert geometry[3].lon == pytest.approx(0.001)
    assert geometry[3].lat == pytest.approx(0.001)


def test_path_to_candidate_combines_geometry_and_distance(
    small_graph: nx.MultiDiGraph,
) -> None:
    candidate = path_to_candidate(small_graph, [NODE_A, NODE_B, NODE_D])

    assert candidate.distance_m == pytest.approx(220.0)
    assert len(candidate.geometry) == 4
    assert candidate.elevation_gain_m == 0.0
    assert candidate.extras is None
