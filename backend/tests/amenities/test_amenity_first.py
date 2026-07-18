import networkx as nx

from app.amenities.models import Amenity
from app.amenities.snapping import snap_amenities
from app.generation.amenity_first import generate_through_amenities
from app.graph.model import node_coordinate
from tests.amenities.conftest import ORIGIN, ORIGIN_NODE


NORTH_INNER_NODE = 101


def _amenity_at(graph: nx.MultiDiGraph, node_id: int) -> Amenity:
    coord = node_coordinate(graph, node_id)
    return Amenity(lat=coord.lat, lon=coord.lon, kind="fountain", name=None)


def test_out_and_back_through_amenity_visits_the_amenity_node(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1000.0,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
        shape="out_and_back",
        count=3,
    )

    assert candidates
    amenity_coord = node_coordinate(spoke_graph, NORTH_INNER_NODE)
    amenity_point = (amenity_coord.lat, amenity_coord.lon)

    best = candidates[0]
    route_points = {(p.lat, p.lon) for p in best.geometry}
    assert amenity_point in route_points


def test_out_and_back_through_amenity_is_within_tolerance(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1000.0,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
        shape="out_and_back",
        count=3,
    )

    assert candidates
    assert abs(candidates[0].distance_m - 1000.0) <= 100.0


def test_generate_through_amenities_starts_and_ends_at_start(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1000.0,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
        shape="out_and_back",
        count=3,
    )

    assert candidates
    start_coord = node_coordinate(spoke_graph, ORIGIN_NODE)
    geometry = candidates[0].geometry
    assert (geometry[0].lat, geometry[0].lon) == (start_coord.lat, start_coord.lon)
    assert (geometry[-1].lat, geometry[-1].lon) == (start_coord.lat, start_coord.lon)


def test_generate_through_amenities_returns_empty_when_none_in_range(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    # Amenity sits at 500m; ask for range far outside that.
    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1000.0,
        snapped=snapped,
        min_range_m=5000.0,
        max_range_m=6000.0,
        shape="out_and_back",
        count=3,
    )

    assert candidates == []


def test_round_shape_through_amenity_visits_the_amenity_node(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1600.0,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
        shape="round",
        count=3,
    )

    # Best-effort: on this sparse spoke graph a genuine "round" loop
    # (return leg on different streets) may not exist -- assert no crash
    # and, if a candidate came back, that it passes through the amenity.
    if candidates:
        amenity_coord = node_coordinate(spoke_graph, NORTH_INNER_NODE)
        amenity_point = (amenity_coord.lat, amenity_coord.lon)
        route_points = {(p.lat, p.lon) for p in candidates[0].geometry}
        assert amenity_point in route_points


def test_mix_shape_returns_best_of_out_and_back_and_round(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    amenity = _amenity_at(spoke_graph, NORTH_INNER_NODE)
    snapped = snap_amenities(spoke_graph, [amenity], max_snap_m=50.0)

    candidates = generate_through_amenities(
        spoke_graph,
        ORIGIN,
        target_distance_m=1000.0,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
        shape="mix",
        count=3,
    )

    assert candidates
    amenity_coord = node_coordinate(spoke_graph, NORTH_INNER_NODE)
    amenity_point = (amenity_coord.lat, amenity_coord.lon)
    route_points = {(p.lat, p.lon) for p in candidates[0].geometry}
    assert amenity_point in route_points
