import networkx as nx

from app.amenities.models import Amenity
from app.amenities.snapping import amenities_in_range, snap_amenities
from app.routing.geometry import destination_point
from tests.amenities.conftest import (
    SNAP_FAR_NODE,
    SNAP_ORIGIN,
    SNAP_ORIGIN_NODE,
)


def test_snap_amenities_snaps_to_nearest_node(
    snap_graph: nx.MultiDiGraph,
) -> None:
    near_origin = destination_point(SNAP_ORIGIN, 0.0, 10.0)
    amenity = Amenity(
        lat=near_origin.lat, lon=near_origin.lon, kind="fountain", name=None
    )

    snapped = snap_amenities(snap_graph, [amenity], max_snap_m=200.0)

    assert len(snapped) == 1
    assert snapped[0].node_id == SNAP_ORIGIN_NODE
    assert snapped[0].snap_distance_m < 200.0


def test_snap_amenities_vectorized_call_handles_multiple(
    snap_graph: nx.MultiDiGraph,
) -> None:
    near_origin = destination_point(SNAP_ORIGIN, 0.0, 10.0)
    near_far = destination_point(SNAP_ORIGIN, 90.0, 5010.0)

    amenities = [
        Amenity(
            lat=near_origin.lat,
            lon=near_origin.lon,
            kind="fountain",
            name="a",
        ),
        Amenity(
            lat=near_far.lat, lon=near_far.lon, kind="restroom", name="b"
        ),
    ]

    snapped = snap_amenities(snap_graph, amenities, max_snap_m=200.0)

    assert {entry.node_id for entry in snapped} == {
        SNAP_ORIGIN_NODE,
        SNAP_FAR_NODE,
    }


def test_snap_amenities_drops_amenities_farther_than_max_snap_m(
    snap_graph: nx.MultiDiGraph,
) -> None:
    # ~1km from the origin node -- far closer to origin than to the far
    # node, but well past a 200m max_snap_m, so it should be dropped
    # entirely (simulates an out-of-graph amenity across water).
    far_from_both = destination_point(SNAP_ORIGIN, 0.0, 1000.0)
    amenity = Amenity(
        lat=far_from_both.lat,
        lon=far_from_both.lon,
        kind="fountain",
        name=None,
    )

    snapped = snap_amenities(snap_graph, [amenity], max_snap_m=200.0)

    assert snapped == []


def test_snap_amenities_empty_input_returns_empty(
    snap_graph: nx.MultiDiGraph,
) -> None:
    assert snap_amenities(snap_graph, [], max_snap_m=200.0) == []


def test_amenities_in_range_filters_by_window_and_sorts_by_midpoint(
    snap_graph: nx.MultiDiGraph,
) -> None:
    near_origin = destination_point(SNAP_ORIGIN, 0.0, 10.0)
    amenities = [
        Amenity(
            lat=near_origin.lat, lon=near_origin.lon, kind="fountain", name="a"
        )
    ]
    snapped = snap_amenities(snap_graph, amenities, max_snap_m=200.0)

    # Two synthetic snapped entries at known graph distances: 1000m
    # (inside [500, 1500]) and 3000m (outside).
    in_window = snapped[0]
    out_of_window = type(in_window)(
        amenity=in_window.amenity,
        node_id=SNAP_FAR_NODE,
        snap_distance_m=0.0,
    )

    dists = {SNAP_ORIGIN_NODE: 1000.0, SNAP_FAR_NODE: 3000.0}

    result = amenities_in_range(
        [in_window, out_of_window], dists, min_range_m=500.0, max_range_m=1500.0
    )

    assert len(result) == 1
    assert result[0].node_id == SNAP_ORIGIN_NODE


def test_amenities_in_range_sorts_closest_to_midpoint_first() -> None:
    from app.amenities.snapping import SnappedAmenity

    amenity = Amenity(lat=0.0, lon=0.0, kind="fountain", name=None)
    far_from_mid = SnappedAmenity(amenity=amenity, node_id=1, snap_distance_m=0.0)
    close_to_mid = SnappedAmenity(amenity=amenity, node_id=2, snap_distance_m=0.0)

    # Range [1000, 2000] -> midpoint 1500. Node 1 at 1050 (450 away),
    # node 2 at 1480 (20 away) -- node 2 should sort first.
    dists = {1: 1050.0, 2: 1480.0}

    result = amenities_in_range(
        [far_from_mid, close_to_mid], dists, min_range_m=1000.0, max_range_m=2000.0
    )

    assert [entry.node_id for entry in result] == [2, 1]


def test_amenities_in_range_skips_amenities_absent_from_dists() -> None:
    amenity = Amenity(lat=0.0, lon=0.0, kind="fountain", name=None)
    from app.amenities.snapping import SnappedAmenity

    unreachable = SnappedAmenity(amenity=amenity, node_id=99, snap_distance_m=0.0)

    result = amenities_in_range(
        [unreachable], dists={}, min_range_m=0.0, max_range_m=1000.0
    )

    assert result == []
