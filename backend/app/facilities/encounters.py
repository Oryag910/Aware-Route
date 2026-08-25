"""Segment-projection facility encounter matching.

Replaces `app/amenities/matching.py`'s nearest-vertex-only matcher, which
gives every physical facility exactly one mile marker and therefore cannot
represent a route that genuinely passes the same facility twice (e.g.
outbound + return on an out-and-back).

For each facility, every rendered geometry segment is treated as a
candidate traversal: the facility is projected onto the segment (local
equirectangular-planar projection, accurate at the scale of one route
segment), and segments whose perpendicular distance is within the access
threshold are grouped by index-contiguity into passes. Each contiguous
pass becomes one `FacilityEncounter`; a route that leaves the facility's
vicinity and later returns produces a second, independent encounter with
the same facility id.
"""

from dataclasses import dataclass
from math import cos, radians

from app.restrooms.geo import cumulative_distances_m
from app.routing.provider import RoutePoint

from app.facilities.models import Facility, FacilityEncounter


# Same "on the route" bar the legacy restroom/amenity matchers use.
FACILITY_ACCESS_THRESHOLD_M = 130.0

_METERS_PER_DEGREE_LAT = 111_320.0


@dataclass(frozen=True)
class _Projection:
    perpendicular_distance_m: float
    fraction_along_segment: float


def _project_onto_segment(
    p0: RoutePoint,
    p1: RoutePoint,
    facility: Facility,
) -> _Projection:
    """Project `facility` onto segment p0->p1 in a local planar frame
    centered on p0. Accurate for segment lengths of at most a few hundred
    meters (ordinary route-geometry spacing); not intended for long-range
    use."""
    lon_scale = cos(radians(p0.lat)) * _METERS_PER_DEGREE_LAT

    def to_xy(lat: float, lon: float) -> tuple[float, float]:
        return (
            (lon - p0.lon) * lon_scale,
            (lat - p0.lat) * _METERS_PER_DEGREE_LAT,
        )

    x1, y1 = to_xy(p1.lat, p1.lon)
    fx, fy = to_xy(facility.lat, facility.lon)

    segment_length_sq = x1 * x1 + y1 * y1
    if segment_length_sq == 0:
        fraction = 0.0
    else:
        fraction = (fx * x1 + fy * y1) / segment_length_sq
        fraction = max(0.0, min(1.0, fraction))

    proj_x, proj_y = fraction * x1, fraction * y1
    dx, dy = fx - proj_x, fy - proj_y
    distance = (dx * dx + dy * dy) ** 0.5

    return _Projection(
        perpendicular_distance_m=distance, fraction_along_segment=fraction
    )


@dataclass(frozen=True)
class _SegmentHit:
    segment_index: int
    perpendicular_distance_m: float
    mile_marker_m: float


def _segment_hits(
    geometry: tuple[RoutePoint, ...],
    route_distances: list[float],
    facility: Facility,
    max_distance_m: float,
) -> list[_SegmentHit]:
    hits: list[_SegmentHit] = []
    for index in range(len(geometry) - 1):
        p0, p1 = geometry[index], geometry[index + 1]
        projection = _project_onto_segment(p0, p1, facility)
        if projection.perpendicular_distance_m > max_distance_m:
            continue
        segment_length = route_distances[index + 1] - route_distances[index]
        mile_marker = (
            route_distances[index]
            + projection.fraction_along_segment * segment_length
        )
        hits.append(
            _SegmentHit(
                segment_index=index,
                perpendicular_distance_m=projection.perpendicular_distance_m,
                mile_marker_m=mile_marker,
            )
        )
    return hits


def _group_contiguous(hits: list[_SegmentHit]) -> list[list[_SegmentHit]]:
    """Group hits into passes by route-segment contiguity, preserving
    route order. A gap of one or more non-qualifying segments starts a
    new pass -- this is what lets an out-and-back's outbound and return
    passes of the same facility become two distinct encounters."""
    groups: list[list[_SegmentHit]] = []
    for hit in sorted(hits, key=lambda h: h.segment_index):
        if groups and hit.segment_index == groups[-1][-1].segment_index + 1:
            groups[-1].append(hit)
        else:
            groups.append([hit])
    return groups


def find_facility_encounters(
    geometry: tuple[RoutePoint, ...],
    facilities: list[Facility],
    max_distance_m: float = FACILITY_ACCESS_THRESHOLD_M,
) -> list[FacilityEncounter]:
    """Every distinct pass of every facility along the finished route,
    ordered by facility id then `mile_marker_m` (which also fixes each
    facility's `encounter_index` sequence)."""
    if len(geometry) < 2:
        return []

    route_distances = cumulative_distances_m(geometry)
    encounters: list[FacilityEncounter] = []

    for facility in facilities:
        hits = _segment_hits(geometry, route_distances, facility, max_distance_m)
        if not hits:
            continue

        for encounter_index, group in enumerate(_group_contiguous(hits)):
            best = min(group, key=lambda h: h.perpendicular_distance_m)
            encounters.append(
                FacilityEncounter(
                    facility=facility,
                    encounter_index=encounter_index,
                    mile_marker_m=best.mile_marker_m,
                    distance_to_route_m=best.perpendicular_distance_m,
                    route_segment_index=best.segment_index,
                )
            )

    encounters.sort(key=lambda e: (e.facility.id, e.encounter_index))
    return encounters
