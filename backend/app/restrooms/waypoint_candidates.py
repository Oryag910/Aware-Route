from collections.abc import Callable
from dataclasses import dataclass

from app.restrooms.models import Restroom
from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.geometry import haversine_m
from app.routing.parallel import run_concurrently
from app.routing.provider import Coordinate, RouteCandidate, RoutingProvider


@dataclass(frozen=True)
class RestroomFirstCandidate:
    """A generated route plus the restroom waypoint it was built
    through — kept together so later stages (e.g. distance repair) can
    preserve the restroom when reshaping the route."""

    candidate: RouteCandidate
    restroom_waypoint: Coordinate


def select_candidate_restrooms(
    restrooms: list[Restroom],
    start: Coordinate,
    min_mile_m: float,
    max_mile_m: float,
    limit: int,
) -> list[Restroom]:
    target_straight_line_m = (min_mile_m + max_mile_m) / 2

    # A route's cumulative distance to any point is always >= the
    # straight-line distance to it (triangle inequality), so a restroom
    # further than max_mile_m away in a straight line can never appear
    # at or before that mile marker on any route -- a necessary, not
    # sufficient, filter.
    eligible = [
        restroom
        for restroom in restrooms
        if haversine_m(
            start,
            Coordinate(lat=restroom.latitude, lon=restroom.longitude),
        )
        <= max_mile_m
    ]

    eligible.sort(
        key=lambda restroom: abs(
            haversine_m(
                start,
                Coordinate(
                    lat=restroom.latitude, lon=restroom.longitude
                ),
            )
            - target_straight_line_m
        )
    )

    return eligible[:limit]


def get_restroom_first_candidates(
    provider: RoutingProvider,
    start: Coordinate,
    restrooms: list[Restroom],
    min_mile_m: float,
    max_mile_m: float,
    limit: int,
) -> list[RestroomFirstCandidate]:
    selected = select_candidate_restrooms(
        restrooms, start, min_mile_m, max_mile_m, limit
    )
    waypoints = [
        Coordinate(lat=restroom.latitude, lon=restroom.longitude)
        for restroom in selected
    ]

    def make_task(
        waypoint: Coordinate,
    ) -> Callable[[], RouteCandidate]:
        return lambda: provider.get_route_through_waypoints(
            [start, waypoint, start]
        )

    tasks = [make_task(waypoint) for waypoint in waypoints]
    results = run_concurrently(tasks)

    candidates: list[RestroomFirstCandidate] = []

    for waypoint, result in zip(waypoints, results):
        if isinstance(result, Exception):
            if not isinstance(
                result, (RouteNotFoundError, RoutingProviderError)
            ):
                raise result

            # This restroom is unreachable -- skip it rather than
            # failing the whole request, same as before parallelism.
            continue

        candidates.append(
            RestroomFirstCandidate(
                candidate=result, restroom_waypoint=waypoint
            )
        )

    return candidates
