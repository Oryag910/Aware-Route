from dataclasses import dataclass

from app.restrooms.models import Restroom
from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.geometry import haversine_m
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

    candidates: list[RestroomFirstCandidate] = []

    for restroom in selected:
        waypoint = Coordinate(
            lat=restroom.latitude, lon=restroom.longitude
        )

        try:
            candidate = provider.get_route_through_waypoints(
                [start, waypoint, start]
            )
        except (RouteNotFoundError, RoutingProviderError):
            continue

        candidates.append(
            RestroomFirstCandidate(
                candidate=candidate, restroom_waypoint=waypoint
            )
        )

    return candidates
