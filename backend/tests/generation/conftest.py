import networkx as nx
import pytest

from app.routing.geometry import destination_point
from app.routing.provider import Coordinate


# A small star graph centred on an origin node with spokes in known
# compass directions at known graph distances. Straight-line spokes mean
# graph distance == haversine distance, so straightness == 1.0 and
# turnaround bearings are exactly the spoke bearings -- ideal for
# exercising the radius window and bearing-sector logic deterministically.
ORIGIN_NODE = 100
ORIGIN = Coordinate(lat=40.75, lon=-73.98)


@pytest.fixture
def star_graph() -> nx.MultiDiGraph:
    """Origin node with straight spokes N/E/S/W at 500m and 1000m.

    Node id encodes (bearing_index, ring): bearing_index in 0..3 for
    N,E,S,W; ring 0 == 500m, ring 1 == 1000m. Spokes are two-hop so the
    500m node sits on the way to the 1000m node."""
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


def _add_bidirectional(
    graph: nx.MultiDiGraph, u: int, v: int, length: float
) -> None:
    graph.add_edge(u, v, key=0, length=length)
    graph.add_edge(v, u, key=0, length=length)
