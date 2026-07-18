from dataclasses import dataclass
from typing import Any

import osmnx as ox

from app.amenities.models import Amenity
from app.graph.model import node_coordinate
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate


DEFAULT_MAX_SNAP_M = 200.0


@dataclass(frozen=True)
class SnappedAmenity:
    amenity: Amenity
    node_id: int
    snap_distance_m: float


def snap_amenities(
    graph: Any,
    amenities: list[Amenity],
    max_snap_m: float = DEFAULT_MAX_SNAP_M,
) -> list[SnappedAmenity]:
    """Snap all amenities to their nearest graph nodes in one vectorized
    call, dropping any that land farther than `max_snap_m` away (across
    water / outside the walk graph)."""
    if not amenities:
        return []

    lons = [amenity.lon for amenity in amenities]
    lats = [amenity.lat for amenity in amenities]

    node_ids = ox.distance.nearest_nodes(graph, X=lons, Y=lats)

    snapped: list[SnappedAmenity] = []

    for amenity, node_id in zip(amenities, node_ids):
        node_id_int = int(node_id)
        node_coord = node_coordinate(graph, node_id_int)
        amenity_coord = Coordinate(lat=amenity.lat, lon=amenity.lon)
        snap_distance_m = haversine_m(amenity_coord, node_coord)

        if snap_distance_m > max_snap_m:
            continue

        snapped.append(
            SnappedAmenity(
                amenity=amenity,
                node_id=node_id_int,
                snap_distance_m=snap_distance_m,
            )
        )

    return snapped


def amenities_in_range(
    snapped: list[SnappedAmenity],
    dists: dict[int, float],
    min_range_m: float,
    max_range_m: float,
) -> list[SnappedAmenity]:
    """Snapped amenities whose graph arc-distance from the start falls
    within [min_range_m, max_range_m], closest to the midpoint first."""
    midpoint_m = (min_range_m + max_range_m) / 2.0

    in_range = [
        entry
        for entry in snapped
        if (dist := dists.get(entry.node_id)) is not None
        and min_range_m <= dist <= max_range_m
    ]

    in_range.sort(key=lambda entry: abs(dists[entry.node_id] - midpoint_m))

    return in_range
