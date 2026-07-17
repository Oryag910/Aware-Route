import pytest

from app.restrooms.models import Restroom
from app.restrooms.waypoint_candidates import (
    get_restroom_first_candidates,
    select_candidate_restrooms,
)
from app.routing.errors import RouteNotFoundError
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


@pytest.fixture(autouse=True)
def _single_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    # FakeWaypointProvider below pops canned responses off a list in
    # call order, which is only deterministic with one worker at a
    # time.
    monkeypatch.setattr(
        "app.routing.parallel.MAX_PARALLEL_ROUTING_CALLS", 1
    )


START = Coordinate(lat=40.70, lon=-74.00)

# ~111,195m per degree of latitude -- used to place restrooms at known
# straight-line distances from START without needing longitude/cos(lat)
# scaling.
METERS_PER_DEGREE_LAT = 111_195.0


def make_restroom(source_id: str, distance_from_start_m: float) -> Restroom:
    lat_offset = distance_from_start_m / METERS_PER_DEGREE_LAT

    return Restroom(
        source_id=source_id,
        facility_name=f"Restroom {source_id}",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=START.lat + lat_offset,
        longitude=START.lon,
    )


def make_candidate() -> RouteCandidate:
    return RouteCandidate(
        geometry=(
            RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
        ),
        distance_m=2000.0,
        elevation_gain_m=0.0,
    )


class FakeWaypointProvider:
    def __init__(
        self,
        responses: list[RouteCandidate | Exception],
    ) -> None:
        self._responses = list(responses)
        self.calls: list[list[Coordinate]] = []

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        raise NotImplementedError(
            "restroom-first generation never calls get_loop"
        )

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        self.calls.append(waypoints)
        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def test_select_candidate_restrooms_excludes_beyond_max_mile() -> None:
    near = make_restroom("near", 500.0)
    mid = make_restroom("mid", 2000.0)
    far = make_restroom("far", 5000.0)
    too_far = make_restroom("too-far", 10_000.0)

    result = select_candidate_restrooms(
        [near, mid, far, too_far],
        START,
        min_mile_m=1000.0,
        max_mile_m=3000.0,
        limit=5,
    )

    # far (5000m) and too_far (10000m) both exceed max_mile_m=3000 --
    # a restroom that far away in a straight line can never appear at
    # or before the 3000m mile marker on any route.
    assert result == [mid, near]


def test_select_candidate_restrooms_sorts_by_closeness_to_range_midpoint() -> None:  # noqa: E501
    near = make_restroom("near", 500.0)
    mid = make_restroom("mid", 2000.0)

    # min/max midpoint is 2000.0 -- mid is an exact match (distance 0),
    # near is 1500m off, so mid should rank first regardless of input
    # order.
    result = select_candidate_restrooms(
        [near, mid],
        START,
        min_mile_m=1000.0,
        max_mile_m=3000.0,
        limit=5,
    )

    assert result == [mid, near]


def test_select_candidate_restrooms_respects_limit() -> None:
    near = make_restroom("near", 500.0)
    mid = make_restroom("mid", 2000.0)

    result = select_candidate_restrooms(
        [near, mid],
        START,
        min_mile_m=1000.0,
        max_mile_m=3000.0,
        limit=1,
    )

    assert result == [mid]


def test_get_restroom_first_candidates_returns_one_per_selected_restroom() -> None:  # noqa: E501
    near = make_restroom("near", 500.0)
    mid = make_restroom("mid", 2000.0)

    candidate_a = make_candidate()
    candidate_b = make_candidate()

    provider = FakeWaypointProvider(responses=[candidate_a, candidate_b])

    result = get_restroom_first_candidates(
        provider,
        START,
        [near, mid],
        min_mile_m=1000.0,
        max_mile_m=3000.0,
        limit=5,
    )

    # mid is selected first (closest to the range midpoint), so it
    # pairs with the first response; each entry keeps the restroom
    # waypoint its route was built through.
    assert [entry.candidate for entry in result] == [
        candidate_a,
        candidate_b,
    ]
    assert result[0].restroom_waypoint == Coordinate(
        lat=mid.latitude, lon=mid.longitude
    )
    assert result[1].restroom_waypoint == Coordinate(
        lat=near.latitude, lon=near.longitude
    )
    assert len(provider.calls) == 2


def test_get_restroom_first_candidates_skips_unreachable_restrooms() -> None:
    near = make_restroom("near", 500.0)
    mid = make_restroom("mid", 2000.0)

    candidate = make_candidate()

    # mid is tried first (closer to the range midpoint) and fails;
    # near should still produce a candidate rather than the whole
    # request failing.
    provider = FakeWaypointProvider(
        responses=[
            RouteNotFoundError("no route to this restroom"),
            candidate,
        ]
    )

    result = get_restroom_first_candidates(
        provider,
        START,
        [near, mid],
        min_mile_m=1000.0,
        max_mile_m=3000.0,
        limit=5,
    )

    assert [entry.candidate for entry in result] == [candidate]
    assert result[0].restroom_waypoint == Coordinate(
        lat=near.latitude, lon=near.longitude
    )
    assert len(provider.calls) == 2
