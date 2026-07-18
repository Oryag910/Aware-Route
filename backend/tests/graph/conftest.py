import networkx as nx
import pytest
from shapely.geometry import LineString


# Node IDs mirror real OSM node IDs (ints) since osmnx's nearest_nodes
# assumes int node identifiers.
NODE_A = 1
NODE_B = 2
NODE_C = 3
NODE_D = 4
NODE_E = 5
NODE_F = 6


@pytest.fixture
def small_graph() -> nx.MultiDiGraph:
    """A tiny synthetic walk graph for exercising app/graph/*.

    Layout (lon=x, lat=y):
        A(0, 0) -- B(0.001, 0) -- C(0.002, 0)
                       |
                    D(0.001, 0.001)
                       |
                    E(0, 0.001)

    A -- E is also connected directly, closing a small loop
    A -> B -> D -> E -> A.

    F(1.0, 1.0) is disconnected from everything else.

    Edge notes:
    - B -> C has two parallel edges (keys 0 and 1) with differing
      `length` (150.0 and 100.0) -- key 1 is the shorter one.
    - B -> D carries a hand-made `geometry` LineString that is stored
      REVERSED (D -> B direction) to exercise re-orientation.
    - A -> B has no `geometry`, exercising the straight-line fallback.
    """
    graph = nx.MultiDiGraph(crs="epsg:4326")

    graph.add_node(NODE_A, x=0.0, y=0.0)
    graph.add_node(NODE_B, x=0.001, y=0.0)
    graph.add_node(NODE_C, x=0.002, y=0.0)
    graph.add_node(NODE_D, x=0.001, y=0.001)
    graph.add_node(NODE_E, x=0.0, y=0.001)
    graph.add_node(NODE_F, x=1.0, y=1.0)

    # A -> B: straight line, no geometry attribute.
    graph.add_edge(NODE_A, NODE_B, key=0, length=100.0)

    # B -> C: parallel edges, key 1 is shorter and should win.
    graph.add_edge(NODE_B, NODE_C, key=0, length=150.0)
    graph.add_edge(NODE_B, NODE_C, key=1, length=100.0)

    # B -> D: geometry stored reversed (D -> B) to test re-orientation.
    # Midpoint bows out to (0.0015, 0.0005) so the fallback straight
    # line and the geometry-based line are distinguishable in tests.
    reversed_geometry = LineString(
        [(0.001, 0.001), (0.0015, 0.0005), (0.001, 0.0)]
    )
    graph.add_edge(
        NODE_B, NODE_D, key=0, length=120.0, geometry=reversed_geometry
    )

    # D -> E: straight line, no geometry.
    graph.add_edge(NODE_D, NODE_E, key=0, length=100.0)

    # E -> A: straight line, no geometry, closes the loop.
    graph.add_edge(NODE_E, NODE_A, key=0, length=100.0)

    return graph
