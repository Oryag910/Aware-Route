from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.flow.interruptions import InterruptionStore
from app.main import (
    app,
    get_eligible_restrooms,
    get_interruption_store,
    get_routing_provider,
)
from app.restrooms.models import Restroom
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
)


client = TestClient(app)

EMPTY_INTERRUPTION_STORE = InterruptionStore(
    signals=(), crossings=(), signal_cell_index={}, crossing_cell_index={}
)


class FakeRoutingProvider:
    def __init__(self, candidate: RouteCandidate) -> None:
        self.candidate = candidate

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        return self.candidate

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        # Restroom-first generation may route through this fixture's
        # restroom (it's within range in several tests) -- simulate
        # waypoint routing landing on the same shape as the blind loop.
        return self.candidate


class SingleSeedRoutingProvider:
    """Returns one candidate for seed==1 and a different candidate for
    every other seed -- lets tests put exactly one candidate shape into
    the matched pool out of the BLIND_CANDIDATE_COUNT candidates
    main.py requests, with the rest all falling into the fallback
    pool."""

    def __init__(
        self,
        first_seed_candidate: RouteCandidate,
        other_seed_candidate: RouteCandidate,
    ) -> None:
        self.first_seed_candidate = first_seed_candidate
        self.other_seed_candidate = other_seed_candidate

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        if seed == 1:
            return self.first_seed_candidate

        return self.other_seed_candidate

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        # Mirrors the "other seed" (fallback) shape, so restroom-first
        # generation doesn't silently duplicate the crafted matched
        # candidate in tests that rely on there being exactly one.
        return self.other_seed_candidate


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()

    # Every test in this module is offline -- override with an empty
    # store so route_interruptions() never touches the real (large)
    # data/interruptions.json file the lazy singleton would otherwise
    # load.
    app.dependency_overrides[get_interruption_store] = (
        lambda: EMPTY_INTERRUPTION_STORE
    )

    yield

    app.dependency_overrides.clear()


def make_candidate() -> RouteCandidate:
    return RouteCandidate(
        geometry=(
            RoutePoint(
                lat=40.70,
                lon=-74.00,
                elevation_m=0.0,
            ),
            RoutePoint(
                lat=40.71,
                lon=-74.00,
                elevation_m=10.0,
            ),
            RoutePoint(
                lat=40.72,
                lon=-74.00,
                elevation_m=5.0,
            ),
        ),
        distance_m=2220.0,
        elevation_gain_m=10.0,
    )


def make_far_candidate() -> RouteCandidate:
    # Same 3-point shape/orientation as make_candidate(), shifted west
    # so its geometry never overlaps make_candidate()'s for similarity
    # purposes, and with a distance far enough from the 2220.0 target
    # to fail the MAX_DISTANCE_ERROR_M=100.0 hard constraint.
    return RouteCandidate(
        geometry=(
            RoutePoint(
                lat=40.70,
                lon=-74.10,
                elevation_m=0.0,
            ),
            RoutePoint(
                lat=40.71,
                lon=-74.10,
                elevation_m=10.0,
            ),
            RoutePoint(
                lat=40.72,
                lon=-74.10,
                elevation_m=5.0,
            ),
        ),
        distance_m=5000.0,
        elevation_gain_m=10.0,
    )


def make_restroom() -> Restroom:
    return Restroom(
        source_id="test-restroom",
        facility_name="Test Park Restroom",
        status="Operational",
        hours_of_operation="8:00 AM - 8:00 PM",
        accessibility="Accessible",
        website=None,
        latitude=40.71,
        longitude=-74.00,
    )


def test_routes_with_restroom_success() -> None:
    candidate = make_candidate()
    restroom = make_restroom()

    fake_provider = FakeRoutingProvider(candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
            # Pinned inside make_restroom()'s "8:00 AM - 8:00 PM" so
            # this test isn't flaky depending on wall-clock time.
            "run_time": "2026-01-01T12:00:00",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) == 1

    route = body[0]

    assert set(route) == {
        "geometry",
        "distance_m",
        "elevation_gain_m",
        "restroom",
        "matched",
        "off_route_distance_m",
        "distance_error_m",
        "mile_range_error_m",
        "distance_error_norm",
        "mile_range_error_norm",
        "elevation_mismatch",
        "repeated_segment_ratio",
        "restroom_confidence",
        "similarity_penalty",
        "composite_score",
        "signal_count",
        "crossing_count",
        "longest_uninterrupted_m",
        "signals_per_km",
        "pedestrian_path_ratio",
        "contains_stairs",
        "sharp_turn_count",
        "u_turn_count",
        "compactness",
        "smoothed_gain_m",
        "climb_count",
        "longest_climb_m",
        "longest_climb_grade_pct",
        "max_grade_pct",
        "archetype",
    }

    assert route["distance_m"] == pytest.approx(2220.0)
    assert route["elevation_gain_m"] == pytest.approx(10.0)
    assert route["matched"] is True
    # The single matched result is by definition the best overall.
    assert route["archetype"] == "best_overall"
    assert route["off_route_distance_m"] == pytest.approx(0.0, abs=5.0)
    assert route["distance_error_m"] == pytest.approx(0.0)
    assert route["mile_range_error_m"] == pytest.approx(0.0)
    assert route["distance_error_norm"] == pytest.approx(0.0)
    assert route["mile_range_error_norm"] == pytest.approx(0.0)
    assert route["elevation_mismatch"] == pytest.approx(0.0)
    assert route["repeated_segment_ratio"] == pytest.approx(0.0)
    assert route["restroom_confidence"] == pytest.approx(1.0)
    assert route["similarity_penalty"] == pytest.approx(0.0)
    assert route["composite_score"] == pytest.approx(0.0)
    # The autouse fixture overrides get_interruption_store with an
    # empty store, and FakeRoutingProvider's candidate carries no
    # extras (ORS extra_info parsing is exercised in test_ors.py, not
    # here).
    assert route["signal_count"] == 0
    assert route["crossing_count"] == 0
    assert route["signals_per_km"] == pytest.approx(0.0)
    assert route["pedestrian_path_ratio"] == pytest.approx(0.0)
    assert route["contains_stairs"] is False
    # make_candidate()'s geometry is a straight 3-point line -- no
    # heading change anywhere, and too degenerate a shape (not a closed
    # loop) for compactness to read as anything but near-zero.
    assert route["sharp_turn_count"] == 0
    assert route["u_turn_count"] == 0
    assert route["compactness"] == pytest.approx(0.0, abs=0.05)

    assert len(route["geometry"]) == 3
    assert route["geometry"][0]["lat"] == pytest.approx(40.70)
    assert route["geometry"][0]["lon"] == pytest.approx(-74.00)

    restroom_response = route["restroom"]

    assert set(restroom_response) == {
        "facility_name",
        "status",
        "hours_of_operation",
        "latitude",
        "longitude",
        "mile_marker_m",
    }

    assert (
        restroom_response["facility_name"]
        == "Test Park Restroom"
    )
    assert restroom_response["status"] == "Operational"
    assert (
        restroom_response["hours_of_operation"]
        == "8:00 AM - 8:00 PM"
    )
    assert restroom_response["latitude"] == pytest.approx(40.71)
    assert restroom_response["longitude"] == pytest.approx(-74.00)
    assert restroom_response["mile_marker_m"] == pytest.approx(
        1112.0,
        abs=5.0,
    )


def test_routes_with_restroom_run_time_outside_hours_returns_422() -> None:
    # The only restroom has hours 8:00 AM - 8:00 PM; a run_time of
    # 11 PM excludes it before candidate generation even runs, so no
    # candidate can match anything -- 422, not a silently empty match.
    candidate = make_candidate()
    restroom = make_restroom()

    fake_provider = FakeRoutingProvider(candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
            "run_time": "2026-01-01T23:00:00",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "No candidate route passed an eligible restroom in range"
        )
    }


def test_routes_with_restroom_run_time_inside_hours_returns_200() -> None:  # noqa: E501
    candidate = make_candidate()
    restroom = make_restroom()

    fake_provider = FakeRoutingProvider(candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
            "run_time": "2026-01-01T12:00:00",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) == 1
    assert body[0]["matched"] is True


def test_routes_with_restroom_returns_422_when_no_match() -> None:
    candidate = make_candidate()
    fake_provider = FakeRoutingProvider(candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = lambda: []

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "No candidate route passed an eligible restroom in range"
        )
    }


def test_routes_with_restroom_backfills_with_fallback_when_understocked() -> None:  # noqa: E501
    matched_candidate = make_candidate()
    fallback_candidate = make_far_candidate()

    matched_restroom = make_restroom()
    fallback_restroom = Restroom(
        source_id="fallback-restroom",
        facility_name="Far Park Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.71,
        longitude=-74.10,
    )

    # Only seed==1 (out of the 8 internal calls main.py makes via
    # BLIND_CANDIDATE_COUNT) produces matched_candidate; seeds 2-8 all
    # produce fallback_candidate. Restroom-first generation also adds
    # one more fallback_candidate (fallback_restroom is too far away in
    # a straight line to be eligible, matched_restroom is). That puts
    # exactly 1 candidate in the matched pool and 8 in the fallback
    # pool -- fewer matched candidates than the requested count=5,
    # forcing backfill.
    fake_provider = SingleSeedRoutingProvider(
        first_seed_candidate=matched_candidate,
        other_seed_candidate=fallback_candidate,
    )

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [matched_restroom, fallback_restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 5,
            # Pinned inside matched_restroom's "8:00 AM - 8:00 PM" so
            # this test isn't flaky depending on wall-clock time.
            "run_time": "2026-01-01T12:00:00",
        },
    )

    assert response.status_code == 200

    body = response.json()

    # Requested count=5, but only 1 candidate is matched -- the
    # remaining 4 slots backfill with fallback_candidate copies
    # (matched=False), all identical since fallback_candidate is the
    # same shape/distance across seeds 2-12.
    assert len(body) == 5

    matched_routes = [route for route in body if route["matched"]]
    fallback_routes = [
        route for route in body if not route["matched"]
    ]

    assert len(matched_routes) == 1
    assert matched_routes[0]["distance_m"] == pytest.approx(2220.0)

    assert len(fallback_routes) == 4
    for route in fallback_routes:
        assert route["distance_m"] == pytest.approx(5000.0)

    # Matched result(s) are ranked ahead of fallback backfill.
    assert body[0]["matched"] is True


def test_routes_with_restroom_count_slices_after_scoring() -> None:
    matched_candidate = make_candidate()
    fallback_candidate = make_far_candidate()

    matched_restroom = make_restroom()
    fallback_restroom = Restroom(
        source_id="fallback-restroom",
        facility_name="Far Park Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.71,
        longitude=-74.10,
    )

    fake_provider = SingleSeedRoutingProvider(
        first_seed_candidate=matched_candidate,
        other_seed_candidate=fallback_candidate,
    )

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [matched_restroom, fallback_restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
            # Pinned inside matched_restroom's "8:00 AM - 8:00 PM" so
            # this test isn't flaky depending on wall-clock time.
            "run_time": "2026-01-01T12:00:00",
        },
    )

    assert response.status_code == 200

    body = response.json()

    # Scoring internally produces 1 matched + 8 fallback candidates (8
    # blind from BLIND_CANDIDATE_COUNT + 1 more fallback from
    # restroom-first generation), but request.count=1 slices the
    # ranked list down to just the top (matched) result -- confirming
    # main.py, not scoring.py, owns the count truncation.
    assert len(body) == 1
    assert body[0]["matched"] is True


def test_routes_with_restroom_422_only_on_zero_restroom_match_not_hard_constraint_failure() -> None:  # noqa: E501
    # A candidate that fails the distance hard constraint but still has
    # an eligible restroom in range must NOT 422 -- it should come back
    # as a matched=False fallback result instead.
    fallback_candidate = make_far_candidate()
    fallback_restroom = Restroom(
        source_id="fallback-restroom",
        facility_name="Far Park Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.71,
        longitude=-74.10,
    )

    fake_provider = FakeRoutingProvider(fallback_candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [fallback_restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) == 1
    assert body[0]["matched"] is False


class BlindVsRestroomFirstProvider:
    """get_loop always returns a candidate whose restroom sits far past
    the requested mile range; get_route_through_waypoints always
    returns a different candidate whose restroom sits squarely in
    range -- isolates restroom-first generation as the only pathway
    that can produce a real match in this scenario."""

    def __init__(
        self,
        blind_candidate: RouteCandidate,
        restroom_first_candidate: RouteCandidate,
    ) -> None:
        self.blind_candidate = blind_candidate
        self.restroom_first_candidate = restroom_first_candidate

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        return self.blind_candidate

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        return self.restroom_first_candidate


def test_restroom_first_generation_fixes_mile_range_error_blind_candidates_miss() -> None:  # noqa: E501
    # Same shape/distance (2220.0m, matches target) as the other blind
    # candidates in this file, but shifted west (-74.10) so its
    # geometry never comes within RESTROOM_PROXIMITY_THRESHOLD_M of
    # good_restroom -- it can only ever match bad_restroom.
    blind_candidate = RouteCandidate(
        geometry=(
            RoutePoint(lat=40.70, lon=-74.10, elevation_m=0.0),
            RoutePoint(lat=40.71, lon=-74.10, elevation_m=10.0),
            RoutePoint(lat=40.72, lon=-74.10, elevation_m=5.0),
        ),
        distance_m=2220.0,
        elevation_gain_m=10.0,
    )
    # Routing through good_restroom as a waypoint reproduces the
    # standard (40.70/40.71/40.72, lon=-74.00) shape used throughout
    # this file, putting good_restroom at mile marker ~1112m -- squarely
    # inside the 0.5-1.0mi (804.67-1609.34m) requested range.
    restroom_first_candidate = make_candidate()

    good_restroom = make_restroom()
    bad_restroom = Restroom(
        source_id="bad-restroom",
        facility_name="Far Mile Marker Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.72,
        longitude=-74.10,
    )

    provider = BlindVsRestroomFirstProvider(
        blind_candidate=blind_candidate,
        restroom_first_candidate=restroom_first_candidate,
    )

    app.dependency_overrides[get_routing_provider] = lambda: provider
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [good_restroom, bad_restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            # Pinned inside good_restroom's "8:00 AM - 8:00 PM" so this
            # test isn't flaky depending on wall-clock time.
            "run_time": "2026-01-01T12:00:00",
            "count": 5,
        },
    )

    assert response.status_code == 200

    body = response.json()

    matched_routes = [route for route in body if route["matched"]]
    fallback_routes = [route for route in body if not route["matched"]]

    # Every blind candidate (all BLIND_CANDIDATE_COUNT seeds return the
    # same shape) only ever matches bad_restroom, whose mile marker is
    # ~614m past restroom_max_mile -- restroom-first generation is the
    # only pathway that produces a route landing good_restroom in range.
    assert len(matched_routes) == 1
    assert (
        matched_routes[0]["restroom"]["facility_name"]
        == "Test Park Restroom"
    )
    assert matched_routes[0]["mile_range_error_m"] == pytest.approx(
        0.0, abs=5.0
    )

    assert len(fallback_routes) > 0
    for route in fallback_routes:
        assert route["mile_range_error_m"] > 500.0


def test_routes_with_restroom_accepts_workout_type() -> None:
    candidate = make_candidate()
    restroom = make_restroom()

    fake_provider = FakeRoutingProvider(candidate)

    app.dependency_overrides[get_routing_provider] = (
        lambda: fake_provider
    )
    app.dependency_overrides[get_eligible_restrooms] = (
        lambda: [restroom]
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "count": 1,
            "run_time": "2026-01-01T12:00:00",
            "workout_type": "tempo",
        },
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
