"""Uniform-grid spatial hash over a facility catalog.

`find_facility_encounters` (encounters.py) needs, for every route
segment, "which facilities could plausibly be within
`FACILITY_ACCESS_THRESHOLD_M` of this segment" -- a brute-force scan of
every facility against every segment is O(facilities x segments) per
candidate route, which dominates route-planning latency once the real
production facility catalog (hundreds of restrooms/water fountains) is
scored against a 12-candidate natural pool with long, many-segment
geometry (see PR discussion: natural_scoring_s of 40-77s on a real
15-mile route).

This index answers that query cheaply by bucketing facilities into a
coarse lat/lon grid and returning only the facilities in cells that
could possibly overlap a segment's bounding box, expanded by the
access threshold. It is a PURE PREFILTER: every facility it returns
still goes through the exact, unchanged `_project_onto_segment` check
in `encounters.py`, so query results are a conservative superset that
can only ever be too large, never too small -- this cannot introduce a
false negative (a real match the brute-force scan would have found but
the indexed path misses).

Built once per `find_facility_encounters`/`score_candidates` call and
reused across every candidate route and every progressive-planner
rescore in the same request (not rebuilt per candidate), since the
facility catalog itself never changes mid-request.
"""

from math import cos, radians

from app.routing.provider import RoutePoint

from app.facilities.models import Facility


_METERS_PER_DEGREE_LAT = 111_320.0

# Coarser than FACILITY_ACCESS_THRESHOLD_M (130m) so a typical query
# only touches a handful of cells; correctness never depends on this
# value since every query buffers by the real search radius regardless
# of cell size (see `_cells_covering`).
_GRID_CELL_SIZE_DEG = 0.01


def _cell_key(lat: float, lon: float) -> tuple[int, int]:
    return (int(lat // _GRID_CELL_SIZE_DEG), int(lon // _GRID_CELL_SIZE_DEG))


class FacilitySpatialIndex:
    def __init__(self, facilities: list[Facility]) -> None:
        self._cells: dict[tuple[int, int], list[Facility]] = {}
        for facility in facilities:
            self._cells.setdefault(_cell_key(facility.lat, facility.lon), []).append(
                facility
            )

    def candidates_near_segment(
        self, p0: RoutePoint, p1: RoutePoint, max_distance_m: float
    ) -> list[Facility]:
        """Every facility that could plausibly be within `max_distance_m`
        of segment p0->p1 -- always a conservative superset of the true
        set, never smaller. Any point on a straight lat/lon segment has
        lat and lon each between its two endpoints' values, so buffering
        the endpoints' bounding box by `max_distance_m` in every
        direction covers the segment's true search area exactly."""
        min_lat = min(p0.lat, p1.lat)
        max_lat = max(p0.lat, p1.lat)
        min_lon = min(p0.lon, p1.lon)
        max_lon = max(p0.lon, p1.lon)

        lat_buffer_deg = max_distance_m / _METERS_PER_DEGREE_LAT

        # Longitude degrees-per-meter grows as cos(lat) shrinks (higher
        # latitude), so using the SMALLER of the two endpoints' cos(lat)
        # gives the LARGER (safe, never-too-small) longitude buffer --
        # conservative regardless of which endpoint is more extreme.
        lon_scale = (
            min(cos(radians(p0.lat)), cos(radians(p1.lat))) * _METERS_PER_DEGREE_LAT
        )
        lon_buffer_deg = (
            max_distance_m / lon_scale if lon_scale > 1e-9 else 180.0
        )  # degenerate near-pole fallback: buffer the whole longitude range

        lo_i, lo_j = _cell_key(min_lat - lat_buffer_deg, min_lon - lon_buffer_deg)
        hi_i, hi_j = _cell_key(max_lat + lat_buffer_deg, max_lon + lon_buffer_deg)

        found: list[Facility] = []
        for i in range(lo_i, hi_i + 1):
            for j in range(lo_j, hi_j + 1):
                bucket = self._cells.get((i, j))
                if bucket:
                    found.extend(bucket)
        return found
