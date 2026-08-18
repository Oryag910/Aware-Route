"""Proves OpenRouteServiceProvider construction is lazy: never on the
local happy path (success or an honest 422), and only when the ORS
branch or the local-fallback path of /routes/with-restroom actually
needs it.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.flow.interruptions import InterruptionStore
from app.graph.loader import GRAPH_PATH
from app.main import app, get_eligible_restrooms, get_interruption_store
from app.rate_limit import rate_limit_dependency
from app.restrooms.models import Restroom
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


client = TestClient(app)

EMPTY_STORE = InterruptionStore(
    signals=(), crossings=(), signal_cell_index={}, crossing_cell_index={}
)

REQUEST_BODY = {
    "start_lat": 40.7812,
    "start_lon": -73.9665,
    "target_distance_m": 8000.0,
    "restroom_min_mile": 1.0,
    "restroom_max_mile": 2.0,
    "elevation_preference": "flat",
    "shape": "mix",
    "count": 3,
}


@pytest.fixture(autouse=True)
def base_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_interruption_store] = lambda: EMPTY_STORE
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    yield
    app.dependency_overrides.clear()


class _OrsMustNotBeConstructed:
    def __init__(self) -> None:
        raise AssertionError(
            "OpenRouteServiceProvider must not be constructed on this path"
        )


def _candidate() -> RouteCandidate:
    return RouteCandidate(
        geometry=(
            RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
            RoutePoint(lat=40.71, lon=-74.00, elevation_m=0.0),
            RoutePoint(lat=40.72, lon=-74.00, elevation_m=0.0),
        ),
        distance_m=2220.0,
        elevation_gain_m=0.0,
    )


class _SpyOrsProvider:
    """Records that ORS was genuinely constructed and used, without
    making any real network calls."""

    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1
        self.calls = 0

    def get_loop(
        self, start: Coordinate, target_distance_m: float, seed: int
    ) -> RouteCandidate:
        self.calls += 1
        return _candidate()

    def get_route_through_waypoints(
        self, waypoints: list[Coordinate]
    ) -> RouteCandidate:
        self.calls += 1
        return _candidate()


def _make_restroom() -> Restroom:
    return Restroom(
        source_id="t",
        facility_name="On-Route Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.71,
        longitude=-74.00,
    )


@pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)
def test_local_success_does_not_construct_ors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local happy path must never build an OpenRouteServiceProvider
    -- and therefore never need ORS_API_KEY -- proven by removing the key
    and making construction itself raise if attempted."""
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")
    monkeypatch.setattr(
        "app.main.OpenRouteServiceProvider", _OrsMustNotBeConstructed
    )
    app.dependency_overrides[get_eligible_restrooms] = lambda: []

    response = client.post("/routes/with-restroom", json=REQUEST_BODY)

    assert response.status_code == 200, response.text
    assert response.headers["X-Route-Engine"] == "local"


@pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)
def test_local_422_does_not_construct_ors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An honest local no-match (422) must also never touch ORS."""
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")
    monkeypatch.setattr(
        "app.main.OpenRouteServiceProvider", _OrsMustNotBeConstructed
    )
    monkeypatch.setattr("app.main.get_fountains", lambda: [])
    app.dependency_overrides[get_eligible_restrooms] = lambda: []

    response = client.post("/routes/with-restroom", json=REQUEST_BODY)

    assert response.status_code == 422, response.text
    assert response.headers["X-Route-Engine"] == "local"


def test_local_failure_constructs_and_uses_ors_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")

    def _boom() -> object:
        raise RuntimeError("simulated graph load failure")

    monkeypatch.setattr("app.main.get_graph", _boom)
    _SpyOrsProvider.instances = 0
    monkeypatch.setattr("app.main.OpenRouteServiceProvider", _SpyOrsProvider)
    app.dependency_overrides[get_eligible_restrooms] = lambda: [
        _make_restroom()
    ]

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "shape": "round",
            "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Route-Engine"] == "ors"
    assert _SpyOrsProvider.instances == 1


def test_ors_engine_constructs_ors_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROUTING_ENGINE=ors (the default) must construct and genuinely use
    the ORS provider on a plain request."""
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "ors")
    _SpyOrsProvider.instances = 0
    monkeypatch.setattr("app.main.OpenRouteServiceProvider", _SpyOrsProvider)
    app.dependency_overrides[get_eligible_restrooms] = lambda: [
        _make_restroom()
    ]

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.70,
            "start_lon": -74.00,
            "target_distance_m": 2220.0,
            "restroom_min_mile": 0.5,
            "restroom_max_mile": 1.0,
            "elevation_preference": "flat",
            "shape": "round",
            "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Route-Engine"] == "ors"
    assert _SpyOrsProvider.instances == 1
