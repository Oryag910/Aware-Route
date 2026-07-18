import networkx as nx

from app.amenities.models import Amenity
from app.amenities.snapping import snap_amenities
from app.generation.engine import generate_amenity_aware, generate_candidates
from app.graph.model import node_coordinate
from tests.generation.conftest import ORIGIN


NORTH_INNER_NODE = 101


def test_generate_amenity_aware_prefers_amenity_passing_candidates(
    star_graph: nx.MultiDiGraph,
) -> None:
    coord = node_coordinate(star_graph, NORTH_INNER_NODE)
    amenity = Amenity(lat=coord.lat, lon=coord.lon, kind="fountain", name=None)
    snapped = snap_amenities(star_graph, [amenity], max_snap_m=50.0)

    candidates = generate_amenity_aware(
        star_graph,
        ORIGIN,
        target_distance_m=1000.0,
        shape="out_and_back",
        count=3,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
    )

    assert candidates
    amenity_point = (coord.lat, coord.lon)
    first_route_points = {(p.lat, p.lon) for p in candidates[0].geometry}
    assert amenity_point in first_route_points


def test_generate_amenity_aware_falls_back_when_no_amenity_in_range(
    star_graph: nx.MultiDiGraph,
) -> None:
    coord = node_coordinate(star_graph, NORTH_INNER_NODE)
    amenity = Amenity(lat=coord.lat, lon=coord.lon, kind="fountain", name=None)
    snapped = snap_amenities(star_graph, [amenity], max_snap_m=50.0)

    # Range far outside the amenity's actual 500m distance -- no
    # amenity-passing candidate should exist, but the ordinary
    # shape-based pool should still fill the result.
    candidates = generate_amenity_aware(
        star_graph,
        ORIGIN,
        target_distance_m=1000.0,
        shape="out_and_back",
        count=3,
        snapped=snapped,
        min_range_m=5000.0,
        max_range_m=6000.0,
    )

    fallback = generate_candidates(
        star_graph, ORIGIN, 1000.0, "out_and_back", 3
    )

    assert candidates
    assert len(candidates) == len(fallback)


def test_generate_amenity_aware_returns_at_most_count(
    star_graph: nx.MultiDiGraph,
) -> None:
    coord = node_coordinate(star_graph, NORTH_INNER_NODE)
    amenity = Amenity(lat=coord.lat, lon=coord.lon, kind="fountain", name=None)
    snapped = snap_amenities(star_graph, [amenity], max_snap_m=50.0)

    candidates = generate_amenity_aware(
        star_graph,
        ORIGIN,
        target_distance_m=1000.0,
        shape="mix",
        count=2,
        snapped=snapped,
        min_range_m=400.0,
        max_range_m=600.0,
    )

    assert len(candidates) <= 2
