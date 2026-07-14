from app.routing.provider import Coordinate, RouteCandidate, RoutingProvider


def get_loop_candidates(
    provider: RoutingProvider,
    start: Coordinate,
    target_distance_m: float,
    count: int = 3,
) -> list[RouteCandidate]:
    if count < 1:
        raise ValueError("count must be at least 1")

    candidates: list[RouteCandidate] = []

    for seed in range(1, count + 1):
        candidate = provider.get_loop(
            start=start,
            target_distance_m=target_distance_m,
            seed=seed,
        )
        candidates.append(candidate)

    return candidates
