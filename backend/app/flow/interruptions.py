import json
from dataclasses import dataclass
from math import floor
from pathlib import Path
from typing import cast

from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


DEFAULT_STORE_PATH = (
    Path(__file__).parents[2] / "data" / "interruptions.json"
)

# Mirrors repeated_segments.py's grid-cell bucketing -- coarse enough to
# keep the index small, fine enough that "threshold_m + cell edge slop"
# still only ever needs the 3x3 neighborhood below.
CELL_SIZE_DEG = 0.0002

DEFAULT_THRESHOLD_M = 25.0


def _grid_cell(
    coordinate: Coordinate,
    cell_size_deg: float = CELL_SIZE_DEG,
) -> tuple[int, int]:
    return (
        floor(coordinate.lat / cell_size_deg),
        floor(coordinate.lon / cell_size_deg),
    )


def _build_cell_index(
    points: tuple[Coordinate, ...],
    cell_size_deg: float = CELL_SIZE_DEG,
) -> dict[tuple[int, int], tuple[int, ...]]:
    buckets: dict[tuple[int, int], list[int]] = {}

    for index, point in enumerate(points):
        cell = _grid_cell(point, cell_size_deg)
        buckets.setdefault(cell, []).append(index)

    return {
        cell: tuple(indices) for cell, indices in buckets.items()
    }


@dataclass(frozen=True)
class InterruptionStore:
    signals: tuple[Coordinate, ...]
    crossings: tuple[Coordinate, ...]
    signal_cell_index: dict[tuple[int, int], tuple[int, ...]]
    crossing_cell_index: dict[tuple[int, int], tuple[int, ...]]


def _empty_store() -> InterruptionStore:
    return InterruptionStore(
        signals=(),
        crossings=(),
        signal_cell_index={},
        crossing_cell_index={},
    )


def load_interruption_store(
    path: Path | None = None,
) -> InterruptionStore:
    store_path = path or DEFAULT_STORE_PATH

    if not store_path.exists():
        return _empty_store()

    with store_path.open() as store_file:
        data: dict[str, object] = json.load(store_file)

    raw_signals = cast(
        list[list[float]], data.get("signals", [])
    )
    raw_crossings = cast(
        list[list[float]], data.get("crossings", [])
    )

    signals = tuple(
        Coordinate(lat=float(point[0]), lon=float(point[1]))
        for point in raw_signals
    )
    crossings = tuple(
        Coordinate(lat=float(point[0]), lon=float(point[1]))
        for point in raw_crossings
    )

    return InterruptionStore(
        signals=signals,
        crossings=crossings,
        signal_cell_index=_build_cell_index(signals),
        crossing_cell_index=_build_cell_index(crossings),
    )


_interruption_store: InterruptionStore | None = None


def get_interruption_store() -> InterruptionStore:
    global _interruption_store

    if _interruption_store is None:
        _interruption_store = load_interruption_store()

    return _interruption_store


def _nearby_indices(
    vertex: RoutePoint,
    points: tuple[Coordinate, ...],
    cell_index: dict[tuple[int, int], tuple[int, ...]],
    threshold_m: float,
) -> set[int]:
    vertex_coordinate = Coordinate(lat=vertex.lat, lon=vertex.lon)
    center_cell = _grid_cell(vertex_coordinate)

    matches: set[int] = set()

    for lat_offset in (-1, 0, 1):
        for lon_offset in (-1, 0, 1):
            cell = (
                center_cell[0] + lat_offset,
                center_cell[1] + lon_offset,
            )

            for index in cell_index.get(cell, ()):
                if (
                    haversine_m(vertex_coordinate, points[index])
                    <= threshold_m
                ):
                    matches.add(index)

    return matches


@dataclass(frozen=True)
class RouteInterruptions:
    signal_count: int
    crossing_count: int
    longest_uninterrupted_m: float
    signals_per_km: float


def _cumulative_distances_m(
    geometry: tuple[RoutePoint, ...],
) -> list[float]:
    """Cumulative route distance at each geometry vertex, starting at
    0.0 for the first vertex."""
    cumulative = [0.0]

    for index in range(len(geometry) - 1):
        segment_m = haversine_m(
            Coordinate(
                lat=geometry[index].lat, lon=geometry[index].lon
            ),
            Coordinate(
                lat=geometry[index + 1].lat,
                lon=geometry[index + 1].lon,
            ),
        )
        cumulative.append(cumulative[-1] + segment_m)

    return cumulative


def route_interruptions(
    geometry: tuple[RoutePoint, ...],
    store: InterruptionStore,
    threshold_m: float = DEFAULT_THRESHOLD_M,
) -> RouteInterruptions:
    if not geometry:
        return RouteInterruptions(
            signal_count=0,
            crossing_count=0,
            longest_uninterrupted_m=0.0,
            signals_per_km=0.0,
        )

    cumulative_m = _cumulative_distances_m(geometry)
    total_distance_m = cumulative_m[-1]

    matched_signal_indices: set[int] = set()
    matched_crossing_indices: set[int] = set()

    # Cumulative distance at each vertex where a signal was first seen
    # nearby -- used below to find the longest gap between signal hits.
    signal_hit_distances_m: list[float] = []

    for vertex_index, vertex in enumerate(geometry):
        signal_hits = _nearby_indices(
            vertex,
            store.signals,
            store.signal_cell_index,
            threshold_m,
        )
        crossing_hits = _nearby_indices(
            vertex,
            store.crossings,
            store.crossing_cell_index,
            threshold_m,
        )

        if signal_hits - matched_signal_indices:
            signal_hit_distances_m.append(cumulative_m[vertex_index])

        matched_signal_indices |= signal_hits
        matched_crossing_indices |= crossing_hits

    if not signal_hit_distances_m:
        longest_uninterrupted_m = total_distance_m
    else:
        gaps_m = [signal_hit_distances_m[0]]

        gaps_m.extend(
            later - earlier
            for earlier, later in zip(
                signal_hit_distances_m, signal_hit_distances_m[1:]
            )
        )

        gaps_m.append(total_distance_m - signal_hit_distances_m[-1])

        longest_uninterrupted_m = max(gaps_m)

    route_km = total_distance_m / 1000.0
    signals_per_km = (
        len(matched_signal_indices) / route_km if route_km > 0 else 0.0
    )

    return RouteInterruptions(
        signal_count=len(matched_signal_indices),
        crossing_count=len(matched_crossing_indices),
        longest_uninterrupted_m=longest_uninterrupted_m,
        signals_per_km=signals_per_km,
    )
