from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RouteExtras, RoutePoint


# ORS foot-walking "waytype" codes (from the extra_info=waytype table).
WAYTYPE_UNKNOWN = 0
WAYTYPE_STATE_ROAD = 1
WAYTYPE_ROAD = 2
WAYTYPE_STREET = 3
WAYTYPE_PATH = 4
WAYTYPE_TRACK = 5
WAYTYPE_CYCLEWAY = 6
WAYTYPE_FOOTWAY = 7
WAYTYPE_STEPS = 8
WAYTYPE_FERRY = 9
WAYTYPE_CONSTRUCTION = 10

# Waytypes considered pedestrian-oriented (i.e. not a general traffic
# road/street), used to compute pedestrian_path_ratio.
PEDESTRIAN_WAYTYPES = frozenset(
    {WAYTYPE_PATH, WAYTYPE_TRACK, WAYTYPE_CYCLEWAY, WAYTYPE_FOOTWAY}
)

# Steps are their own waytype code, not a surface -- a route with any
# steps segment forces contains_stairs=True.
STAIRS_WAYTYPE = WAYTYPE_STEPS


def _as_coordinate(point: RoutePoint) -> Coordinate:
    return Coordinate(lat=point.lat, lon=point.lon)


def pedestrian_path_ratio(
    geometry: tuple[RoutePoint, ...],
    extras: RouteExtras | None,
) -> float:
    """Fraction of route distance covered by pedestrian-oriented waytype
    segments (path/track/cycleway/footway), weighted by the haversine
    distance between consecutive geometry points."""
    if extras is None or len(geometry) < 2:
        return 0.0

    segment_distances_m = [
        haversine_m(
            _as_coordinate(geometry[index]),
            _as_coordinate(geometry[index + 1]),
        )
        for index in range(len(geometry) - 1)
    ]

    total_distance_m = sum(segment_distances_m)

    if total_distance_m <= 0.0:
        return 0.0

    pedestrian_distance_m = 0.0

    for start_index, end_index, code in extras.waytype_segments:
        if code not in PEDESTRIAN_WAYTYPES:
            continue

        clamped_end_index = min(end_index, len(segment_distances_m))

        pedestrian_distance_m += sum(
            segment_distances_m[start_index:clamped_end_index]
        )

    return pedestrian_distance_m / total_distance_m


def contains_stairs(
    geometry: tuple[RoutePoint, ...],
    extras: RouteExtras | None,
) -> bool:
    if extras is None:
        return False

    return any(
        code == STAIRS_WAYTYPE
        for _start_index, _end_index, code in extras.waytype_segments
    )
