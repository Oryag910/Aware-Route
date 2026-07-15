from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import (
    app,
    get_eligible_restrooms,
    get_routing_provider,
)
from app.restrooms.models import Restroom
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
)


client = TestClient(app)


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


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()

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
        "distance_error_m",
        "mile_range_error_m",
        "distance_error_norm",
        "mile_range_error_norm",
        "elevation_mismatch",
        "repeated_segment_ratio",
        "restroom_confidence",
        "similarity_penalty",
        "composite_score",
    }

    assert route["distance_m"] == pytest.approx(2220.0)
    assert route["elevation_gain_m"] == pytest.approx(10.0)
    assert route["distance_error_m"] == pytest.approx(0.0)
    assert route["mile_range_error_m"] == pytest.approx(0.0)
    assert route["distance_error_norm"] == pytest.approx(0.0)
    assert route["mile_range_error_norm"] == pytest.approx(0.0)
    assert route["elevation_mismatch"] == pytest.approx(0.0)
    assert route["repeated_segment_ratio"] == pytest.approx(0.0)
    assert route["restroom_confidence"] == pytest.approx(1.0)
    assert route["similarity_penalty"] == pytest.approx(0.0)
    assert route["composite_score"] == pytest.approx(0.0)

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
