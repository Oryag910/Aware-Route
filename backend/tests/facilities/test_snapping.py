from app.facilities.models import Facility
from app.facilities.snapping import snap_facilities
from app.graph.loader import GRAPH_PATH, get_graph
from app.graph.model import node_coordinate
from app.graph.distances import nearest_node
from app.routing.provider import Coordinate

import pytest


pytestmark = pytest.mark.skipif(
    not GRAPH_PATH.exists(), reason="graph artifact not built"
)


def test_snap_facility_on_graph() -> None:
    graph = get_graph()
    start_node = nearest_node(graph, Coordinate(lat=40.7812, lon=-73.9665))
    coord = node_coordinate(graph, start_node)

    facility = Facility(
        id="restroom:1",
        kind="restroom",
        lat=coord.lat,
        lon=coord.lon,
        name=None,
        status=None,
        hours_of_operation=None,
        source="test",
    )

    snapped = snap_facilities(graph, [facility])
    assert len(snapped) == 1
    assert snapped[0].node_id == start_node
    assert snapped[0].snap_distance_m < 5.0


def test_snap_facility_far_away_dropped() -> None:
    graph = get_graph()
    facility = Facility(
        id="restroom:far",
        kind="restroom",
        lat=0.0,
        lon=0.0,
        name=None,
        status=None,
        hours_of_operation=None,
        source="test",
    )
    assert snap_facilities(graph, [facility]) == []


def test_snap_empty_list() -> None:
    graph = get_graph()
    assert snap_facilities(graph, []) == []
