from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Coordinate:
    lat: float
    lon: float


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    elevation_m: float


@dataclass(frozen=True)
class RouteExtras:
    """ORS "extra_info" segments, each a (start_index, end_index, code)
    tuple spanning a range of geometry point indices. Codes are the ORS
    foot-walking waytype/surface/steepness tables -- see
    app/flow/surfaces.py for the ones this codebase interprets."""
    waytype_segments: tuple[tuple[int, int, int], ...]
    surface_segments: tuple[tuple[int, int, int], ...]
    steepness_segments: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class RouteCandidate:
    geometry: tuple[RoutePoint, ...]
    distance_m: float
    elevation_gain_m: float
    extras: RouteExtras | None = None


class RoutingProvider(Protocol):
    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        ...

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        ...