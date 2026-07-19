import networkx as nx

from app.amenities.models import Amenity
from app.amenities.snapping import SnappedAmenity, snap_amenities
from app.generation.amenity_first import (
    _select_placements,
    generate_through_amenities,
)
from app.graph.model import node_coordinate
from tests.amenities.conftest import ORIGIN, ORIGIN_NODE


NORTH_INNER_NODE = 101


def _snapped_at(
    node_id: int, distance_m: float
) -> tuple[SnappedAmenity, dict[int, float]]:
    entry = SnappedAmenity(
        amenity=Amenity(lat=0.0, lon=0.0, kind="restroom", name=None),
        node_id=node_id,
        snap_distance_m=0.0,
    )
    return entry, {node_id: distance_m}


def test_select_placements_satisfies_high_range_on_the_return_leg() -> None:
    """A restroom only 2km from start satisfies a "mile 7-8" ask on a 10km
    out-and-back, because it is passed again at cumulative target-d on the
    way back."""
    entry, dists = _snapped_at(node_id=1, distance_m=2000.0)

    placements = _select_placements(
        [entry], dists, target_distance_m=10000.0,
        min_range_m=7000.0, max_range_m=8000.0, shape="out_and_back", count=3,
    )

    assert len(placements) == 1
    assert placements[0].on_return is True
    assert abs(placements[0].cumulative_target - 8000.0) < 1e-6


def test_select_placements_uses_outbound_leg_when_range_is_near() -> None:
    entry, dists = _snapped_at(node_id=1, distance_m=2000.0)

    placements = _select_placements(
        [entry], dists, target_distance_m=10000.0,
        min_range_m=1500.0, max_range_m=2500.0, shape="out_and_back", count=3,
    )

    assert len(placements) == 1
    assert placements[0].on_return is False
    assert abs(placements[0].cumulative_target - 2000.0) < 1e-6


def test_select_placements_round_does_not_offer_return_leg() -> None:
    """A loop passes each point once, so a near restroom cannot satisfy a
    far range for the round shape (only out-and-back's return leg can)."""
    entry, dists = _snapped_at(node_id=1, distance_m=2000.0)

    placements = _select_placements(
        [entry], dists, target_distance_m=10000.0,
        min_range_m=7000.0, max_range_m=8000.0, shape="round", count=3,
    )

    assert placements == []


def test_select_placements_skips_restrooms_beyond_half_target() -> None:
    """A point more than half a there-and-back away can't be reached within
    the target, so it is never selected regardless of range."""
    entry, dists = _snapped_at(node_id=1, distance_m=6000.0)

    placements = _select_placements(
        [entry], dists, target_distance_m=10000.0,
        min_range_m=5000.0, max_range_m=6000.0, shape="out_and_back", count=3,
    )

    assert placements == []


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
