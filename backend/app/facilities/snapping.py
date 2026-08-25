"""Snap `Facility` objects onto the walk graph.

Only the constrained planners (round/out-and-back, Phase 3/4) need this
-- natural-match scoring (`app/facilities/scoring.py`) works directly off
finished route geometry and never snaps anything. Mirrors
`app/amenities/snapping.py`'s vectorized nearest-node approach, adapted
to the new `Facility` domain type.
"""

from typing import Any

import osmnx as ox

from app.graph.model import node_coordinate
from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate

from app.facilities.models import Facility, SnappedFacility


DEFAULT_MAX_SNAP_M = 200.0


def snap_facilities(
    graph: Any,
    facilities: list[Facility],
    max_snap_m: float = DEFAULT_MAX_SNAP_M,
) -> list[SnappedFacility]:
    """Snap all facilities to their nearest graph nodes in one vectorized
    call, dropping any that land farther than `max_snap_m` away."""
    if not facilities:
        return []

    lons = [facility.lon for facility in facilities]
    lats = [facility.lat for facility in facilities]

    node_ids = ox.distance.nearest_nodes(graph, X=lons, Y=lats)

    snapped: list[SnappedFacility] = []
    for facility, node_id in zip(facilities, node_ids):
        node_id_int = int(node_id)
        node_coord = node_coordinate(graph, node_id_int)
        facility_coord = Coordinate(lat=facility.lat, lon=facility.lon)
        snap_distance_m = haversine_m(facility_coord, node_coord)

        if snap_distance_m > max_snap_m:
            continue

        snapped.append(
            SnappedFacility(
                facility=facility, node_id=node_id_int, snap_distance_m=snap_distance_m
            )
        )

    return snapped
