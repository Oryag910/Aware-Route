import threading

import pytest

from app.routing.candidates import get_loop_candidates
from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
)


@pytest.fixture(autouse=True)
def _single_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fakes below record call order via a shared list, which is only
    # deterministic with one worker at a time.
    monkeypatch.setattr(
        "app.routing.parallel.MAX_PARALLEL_ROUTING_CALLS", 1
    )


class FakeRoutingProvider:
    def __init__(
        self,
        failing_seeds: frozenset[int] = frozenset(),
        failure: Exception | None = None,
    ) -> None:
        self.seeds: list[int] = []
        self._failing_seeds = failing_seeds
        self._failure = failure or RouteNotFoundError("no route")
        self._lock = threading.Lock()

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        with self._lock:
            self.seeds.append(seed)

        if seed in self._failing_seeds:
            raise self._failure

        return RouteCandidate(
            geometry=(
                RoutePoint(
                    lat=start.lat,
                    lon=start.lon,
                    elevation_m=0.0,
                ),
            ),
            distance_m=target_distance_m,
            elevation_gain_m=float(seed),
        )

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        raise NotImplementedError(
            "get_loop_candidates never calls this"
        )


def test_get_loop_candidates_uses_sequential_seeds() -> None:
    provider = FakeRoutingProvider()
    start = Coordinate(lat=40.7128, lon=-74.0060)

    candidates = get_loop_candidates(
        provider=provider,
        start=start,
        target_distance_m=5000.0,
        count=3,
    )

    assert provider.seeds == [1, 2, 3]
    assert len(candidates) == 3


def test_get_loop_candidates_skips_individual_seed_failures() -> None:
    # Seed 2 fails (e.g. unroutable loop) but seeds 1 and 3 still
    # succeed -- the request shouldn't be sunk by one bad seed.
    provider = FakeRoutingProvider(failing_seeds=frozenset({2}))
    start = Coordinate(lat=40.7128, lon=-74.0060)

    candidates = get_loop_candidates(
        provider=provider,
        start=start,
        target_distance_m=5000.0,
        count=3,
    )

    assert len(candidates) == 2
    assert sorted(provider.seeds) == [1, 2, 3]


def test_get_loop_candidates_raises_when_every_seed_fails() -> None:
    provider = FakeRoutingProvider(
        failing_seeds=frozenset({1, 2, 3}),
        failure=RoutingProviderError("rate limited"),
    )
    start = Coordinate(lat=40.7128, lon=-74.0060)

    with pytest.raises(RoutingProviderError):
        get_loop_candidates(
            provider=provider,
            start=start,
            target_distance_m=5000.0,
            count=3,
        )


@pytest.mark.parametrize("count", [0, -1])
def test_get_loop_candidates_rejects_invalid_count(
    count: int,
) -> None:
    provider = FakeRoutingProvider()
    start = Coordinate(lat=40.7128, lon=-74.0060)

    with pytest.raises(ValueError):
        get_loop_candidates(
            provider=provider,
            start=start,
            target_distance_m=5000.0,
            count=count,
        )
