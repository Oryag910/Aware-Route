import pytest

from app.routing.candidates import get_loop_candidates
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
)


class FakeRoutingProvider:
    def __init__(self) -> None:
        self.seeds: list[int] = []

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        self.seeds.append(seed)

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
