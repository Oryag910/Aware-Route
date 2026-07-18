import networkx as nx
import pytest

from app.routing.geometry import destination_point
from app.routing.provider import Coordinate


ORIGIN_NODE = 100
ORIGIN = Coordinate(lat=40.75, lon=-73.98)


def _add_bidirectional(
    graph: nx.MultiDiGraph, u: int, v: int, length: float
) -> None:
    graph.add_edge(u, v, key=0, length=length)
    graph.add_edge(v, u, key=0, length=length)


SNAP_ORIGIN_NODE = 200
SNAP_ORIGIN = Coordinate(lat=40.75, lon=-73.98)
SNAP_FAR_NODE = 201


@pytest.fixture
def snap_graph() -> nx.MultiDiGraph:
    """Two nodes for exercising snap_amenities: one at the origin, one
    ~5km east (far enough that an amenity near it, but not near enough
    to be *at* it, exceeds a small max_snap_m)."""
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(SNAP_ORIGIN_NODE, x=SNAP_ORIGIN.lon, y=SNAP_ORIGIN.lat)

    far = destination_point(SNAP_ORIGIN, 90.0, 5000.0)
    graph.add_node(SNAP_FAR_NODE, x=far.lon, y=far.lat)

    _add_bidirectional(graph, SNAP_ORIGIN_NODE, SNAP_FAR_NODE, 5000.0)

    return graph


@pytest.fixture
def spoke_graph() -> nx.MultiDiGraph:
    """Same star layout as tests/generation/conftest.py's star_graph:
    origin with straight N/E/S/W spokes, nodes at 500m and 1000m rings.

    Reproduced locally (rather than imported) so tests/amenities stays
    independent of tests/generation's fixture module.
    """
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(ORIGIN_NODE, x=ORIGIN.lon, y=ORIGIN.lat)

    bearings = {0: 0.0, 1: 90.0, 2: 180.0, 3: 270.0}

    for bearing_index, bearing in bearings.items():
        inner = destination_point(ORIGIN, bearing, 500.0)
        outer = destination_point(ORIGIN, bearing, 1000.0)

        inner_id = 100 + bearing_index * 10 + 1
        outer_id = 100 + bearing_index * 10 + 2

        graph.add_node(inner_id, x=inner.lon, y=inner.lat)
        graph.add_node(outer_id, x=outer.lon, y=outer.lat)

        _add_bidirectional(graph, ORIGIN_NODE, inner_id, 500.0)
        _add_bidirectional(graph, inner_id, outer_id, 500.0)

    return graph
