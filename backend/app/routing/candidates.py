from collections.abc import Callable

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.parallel import run_concurrently
from app.routing.provider import Coordinate, RouteCandidate, RoutingProvider


def get_loop_candidates(
    provider: RoutingProvider,
    start: Coordinate,
    target_distance_m: float,
    count: int = 3,
) -> list[RouteCandidate]:
    if count < 1:
        raise ValueError("count must be at least 1")

    def make_task(
        seed: int,
    ) -> Callable[[], RouteCandidate]:
        return lambda: provider.get_loop(
            start=start,
            target_distance_m=target_distance_m,
            seed=seed,
        )

    tasks = [make_task(seed) for seed in range(1, count + 1)]
    results = run_concurrently(tasks)

    candidates: list[RouteCandidate] = []
    first_exception: Exception | None = None

    for result in results:
        if isinstance(result, Exception):
            # An individual seed failing (unroutable loop, transient
            # provider hiccup) shouldn't sink the whole request as
            # long as at least one other seed produced a candidate.
            if not isinstance(
                result, (RouteNotFoundError, RoutingProviderError)
            ):
                raise result

            if first_exception is None:
                first_exception = result

            continue

        candidates.append(result)

    if not candidates and first_exception is not None:
        raise first_exception

    return candidates
