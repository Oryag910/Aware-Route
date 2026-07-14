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
class RouteCandidate:
    geometry: tuple[RoutePoint, ...]
    distance_m: float
    elevation_gain_m: float


class RoutingProvider(Protocol):
    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        ...