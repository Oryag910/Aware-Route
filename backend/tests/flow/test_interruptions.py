import json
from pathlib import Path

import pytest

from app.flow.interruptions import (
    InterruptionStore,
    _build_cell_index,
    load_interruption_store,
    route_interruptions,
)
from app.routing.provider import Coordinate, RoutePoint


def make_store(
    signals: list[Coordinate],
    crossings: list[Coordinate],
) -> InterruptionStore:
    return InterruptionStore(
        signals=tuple(signals),
        crossings=tuple(crossings),
        signal_cell_index=_build_cell_index(tuple(signals)),
        crossing_cell_index=_build_cell_index(tuple(crossings)),
    )


def make_geometry(
    lat_steps: list[float],
    lon: float = -74.00,
) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat, lon=lon, elevation_m=0.0)
        for lat in lat_steps
    )


def test_load_interruption_store_missing_file_is_empty(
    tmp_path: Path,
) -> None:
    store = load_interruption_store(tmp_path / "does-not-exist.json")

    assert store.signals == ()
    assert store.crossings == ()
    assert store.signal_cell_index == {}
    assert store.crossing_cell_index == {}


def test_load_interruption_store_reads_file(tmp_path: Path) -> None:
    store_path = tmp_path / "interruptions.json"
    store_path.write_text(
        json.dumps(
            {
                "signals": [[40.70, -74.00]],
                "crossings": [[40.71, -74.00], [40.72, -74.00]],
            }
        )
    )

    store = load_interruption_store(store_path)

    assert store.signals == (Coordinate(lat=40.70, lon=-74.00),)
    assert store.crossings == (
        Coordinate(lat=40.71, lon=-74.00),
        Coordinate(lat=40.72, lon=-74.00),
    )


def test_route_interruptions_empty_store() -> None:
    geometry = make_geometry([40.70, 40.701, 40.702])
    store = InterruptionStore((), (), {}, {})

    result = route_interruptions(geometry, store)

    assert result.signal_count == 0
    assert result.crossing_count == 0
    assert result.signals_per_km == pytest.approx(0.0)
    # No signals at all -- the longest uninterrupted stretch is the
    # whole route.
    total_m = 2 * 111.194  # two 0.001-degree-lat segments
    assert result.longest_uninterrupted_m == pytest.approx(
        total_m, rel=1e-3
    )


def test_route_interruptions_counts_unique_points_only_once() -> None:
    # A single signal sits within threshold_m of two consecutive
    # geometry vertices -- it must be counted once, not twice.
    geometry = make_geometry([40.70, 40.7002, 40.701])
    signal = Coordinate(lat=40.7001, lon=-74.00)
    store = make_store(signals=[signal], crossings=[])

    result = route_interruptions(geometry, store, threshold_m=25.0)

    assert result.signal_count == 1


def test_route_interruptions_crossing_count_independent_of_signals() -> (
    None
):
    geometry = make_geometry([40.70, 40.701])
    signal = Coordinate(lat=40.70, lon=-74.00)
    crossing = Coordinate(lat=40.701, lon=-74.00)
    store = make_store(signals=[signal], crossings=[crossing])

    result = route_interruptions(geometry, store, threshold_m=25.0)

    assert result.signal_count == 1
    assert result.crossing_count == 1


def test_route_interruptions_ignores_far_points() -> None:
    geometry = make_geometry([40.70, 40.701])
    far_signal = Coordinate(lat=41.0, lon=-74.00)
    store = make_store(signals=[far_signal], crossings=[])

    result = route_interruptions(geometry, store, threshold_m=25.0)

    assert result.signal_count == 0


def test_route_interruptions_longest_gap_includes_start_and_end() -> (
    None
):
    # Signal sits near the middle vertex only -- gaps are
    # start-to-signal and signal-to-end.
    geometry = make_geometry([40.70, 40.702, 40.704, 40.706])
    signal = Coordinate(lat=40.702, lon=-74.00)
    store = make_store(signals=[signal], crossings=[])

    result = route_interruptions(geometry, store, threshold_m=25.0)

    assert result.signal_count == 1

    segment_m = 111.194 * 2  # 0.002 deg lat segments
    start_to_signal_m = segment_m
    signal_to_end_m = segment_m * 2

    assert result.longest_uninterrupted_m == pytest.approx(
        signal_to_end_m, rel=1e-2
    )
    assert result.longest_uninterrupted_m > start_to_signal_m


def test_route_interruptions_signals_per_km() -> None:
    # Two 111.19m segments -> ~0.2224 km total route, one signal hit.
    geometry = make_geometry([40.70, 40.701, 40.702])
    signal = Coordinate(lat=40.70, lon=-74.00)
    store = make_store(signals=[signal], crossings=[])

    result = route_interruptions(geometry, store, threshold_m=25.0)

    route_km = (2 * 111.194) / 1000.0
    assert result.signals_per_km == pytest.approx(
        1 / route_km, rel=1e-2
    )


def test_route_interruptions_degenerate_route_signals_per_km_zero() -> (
    None
):
    geometry = make_geometry([40.70])
    store = InterruptionStore((), (), {}, {})

    result = route_interruptions(geometry, store)

    assert result.signals_per_km == pytest.approx(0.0)
    assert result.longest_uninterrupted_m == pytest.approx(0.0)


def test_route_interruptions_empty_geometry() -> None:
    store = InterruptionStore((), (), {}, {})

    result = route_interruptions((), store)

    assert result.signal_count == 0
    assert result.crossing_count == 0
    assert result.longest_uninterrupted_m == pytest.approx(0.0)
    assert result.signals_per_km == pytest.approx(0.0)
