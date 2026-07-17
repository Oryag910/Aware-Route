from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.geometry import bearing_deg, destination_point, haversine_m
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
    RoutingProvider,
)


# A candidate within this many meters of the target is already accurate
# enough — matches the hard constraint used downstream in scoring.py.
MAX_DISTANCE_ERROR_M = 100.0

# Candidates further off than this fraction of the target are considered
# too far gone to fix cheaply with a single nudged waypoint, and are left
# as-is for the fallback ranking to handle.
NEAR_MISS_RATIO = 0.15

# Bound on how many corrective ORS calls a single candidate can use.
MAX_REPAIR_ROUNDS = 3

# Shared budget across all near-miss candidates in one request, so a
# request with many near-misses can't blow through the ORS rate limit.
MAX_REPAIR_CALLS_PER_REQUEST = 8


def _is_near_miss(candidate: RouteCandidate, target_distance_m: float) -> bool:
    distance_error = abs(candidate.distance_m - target_distance_m)

    return (
        MAX_DISTANCE_ERROR_M
        < distance_error
        <= NEAR_MISS_RATIO * target_distance_m
    )


def _furthest_point(
    geometry: tuple[RoutePoint, ...],
    start: Coordinate,
) -> Coordinate:
    points = [
        Coordinate(lat=point.lat, lon=point.lon) for point in geometry
    ]

    return max(points, key=lambda point: haversine_m(start, point))


def _repair_candidate(
    provider: RoutingProvider,
    candidate: RouteCandidate,
    start: Coordinate,
    target_distance_m: float,
    max_rounds: int,
) -> tuple[RouteCandidate, int]:
    best = candidate
    best_error = abs(candidate.distance_m - target_distance_m)

    anchor_point = _furthest_point(candidate.geometry, start)
    bearing = bearing_deg(start, anchor_point)
    anchor_distance_m = haversine_m(start, anchor_point)

    calls_used = 0
    nudge_m = (target_distance_m - candidate.distance_m) / 2

    for _ in range(max_rounds):
        anchor_distance_m += nudge_m

        if anchor_distance_m <= 0:
            break

        new_anchor = destination_point(start, bearing, anchor_distance_m)

        try:
            attempt = provider.get_route_through_waypoints(
                [start, new_anchor, start]
            )
        except (RouteNotFoundError, RoutingProviderError):
            break

        calls_used += 1
        attempt_error = abs(attempt.distance_m - target_distance_m)

        if attempt_error < best_error:
            best = attempt
            best_error = attempt_error

        if best_error <= MAX_DISTANCE_ERROR_M:
            break

        nudge_m = (target_distance_m - attempt.distance_m) / 2

    return best, calls_used


def repair_near_miss_candidates(
    provider: RoutingProvider,
    candidates: list[RouteCandidate],
    start: Coordinate,
    target_distance_m: float,
) -> list[RouteCandidate]:
    repaired: list[RouteCandidate] = []
    remaining_calls = MAX_REPAIR_CALLS_PER_REQUEST

    for candidate in candidates:
        if remaining_calls <= 0 or not _is_near_miss(
            candidate, target_distance_m
        ):
            repaired.append(candidate)
            continue

        corrected, calls_used = _repair_candidate(
            provider,
            candidate,
            start,
            target_distance_m,
            max_rounds=min(MAX_REPAIR_ROUNDS, remaining_calls),
        )
        remaining_calls -= calls_used
        repaired.append(corrected)

    return repaired
