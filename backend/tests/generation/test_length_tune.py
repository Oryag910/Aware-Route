import networkx as nx

from app.generation.length_tune import tune_to_target
from app.graph.distances import single_source_distances
from app.graph.model import path_to_candidate
from app.routing.geometry import destination_point
from app.routing.provider import Coordinate


ORIGIN = Coordinate(lat=40.75, lon=-73.98)
ORIGIN_NODE = 1


def _tune_graph() -> nx.MultiDiGraph:
    """Origin with a short east target node (300m) used as the base
    route, plus a north spur node (200m) used to lengthen it."""
    graph = nx.MultiDiGraph(crs="epsg:4326")
    graph.add_node(ORIGIN_NODE, x=ORIGIN.lon, y=ORIGIN.lat)

    east = destination_point(ORIGIN, 90.0, 300.0)
    north = destination_point(ORIGIN, 0.0, 200.0)

    graph.add_node(2, x=east.lon, y=east.lat)
    graph.add_node(3, x=north.lon, y=north.lat)

    for u, v, length in ((ORIGIN_NODE, 2, 300.0), (ORIGIN_NODE, 3, 200.0)):
        graph.add_edge(u, v, key=0, length=length)
        graph.add_edge(v, u, key=0, length=length)

    return graph


def test_within_tolerance_returns_unchanged() -> None:
    graph = _tune_graph()
    node_path = [ORIGIN_NODE, 2, ORIGIN_NODE]  # 600m out-and-back
    candidate = path_to_candidate(graph, node_path)

    tuned = tune_to_target(
        graph,
        ORIGIN_NODE,
        candidate,
        node_path,
        target_distance_m=600.0,
        tolerance_m=100.0,
    )
    assert tuned is candidate


def test_too_short_splices_spur() -> None:
    graph = _tune_graph()
    # Base route: 600m out-and-back east. Target 1000m -> 400m deficit ->
    # a ~400m spur (200m north, walked twice) is spliced in front.
    node_path = [ORIGIN_NODE, 2, ORIGIN_NODE]
    candidate = path_to_candidate(graph, node_path)
    dists = single_source_distances(graph, ORIGIN_NODE)

    tuned = tune_to_target(
        graph,
        ORIGIN_NODE,
        candidate,
        node_path,
        target_distance_m=1000.0,
        tolerance_m=100.0,
        dists=dists,
    )

    # 600m base + 400m spur == 1000m, within tolerance.
    assert abs(tuned.distance_m - 1000.0) <= 100.0
    assert tuned.distance_m > candidate.distance_m


def test_too_long_returns_unchanged() -> None:
    graph = _tune_graph()
    node_path = [ORIGIN_NODE, 2, ORIGIN_NODE]  # 600m
    candidate = path_to_candidate(graph, node_path)

    # A spur can only add length, so an overshoot is left to the caller.
    tuned = tune_to_target(
        graph,
        ORIGIN_NODE,
        candidate,
        node_path,
        target_distance_m=300.0,
        tolerance_m=100.0,
    )
    assert tuned is candidate
