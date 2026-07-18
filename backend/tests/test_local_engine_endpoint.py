"""Endpoint tests for ROUTING_ENGINE=local and its ORS fallback.

The success test exercises the real committed graph artifact + real
fountains (restrooms overridden empty, so no Supabase); it's skipped if
the artifact isn't present. The fallback test needs no graph -- it forces
a local-engine failure and asserts the request still succeeds via ORS.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.flow.interruptions import InterruptionStore
from app.graph.loader import GRAPH_PATH
from app.main import (
    app,
    get_eligible_restrooms,
    get_interruption_store,
    get_routing_provider,
)
from app.rate_limit import rate_limit_dependency
from app.restrooms.models import Restroom
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


client = TestClient(app)

EMPTY_STORE = InterruptionStore(
    signals=(), crossings=(), signal_cell_index={}, crossing_cell_index={}
)


@pytest.fixture(autouse=True)
def base_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_interruption_store] = lambda: EMPTY_STORE
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)
@pytest.mark.parametrize("shape", ["round", "out_and_back", "mix"])
def test_local_engine_returns_matched_routes(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")
    # Fountains-only amenity pool -> no Supabase needed.
    app.dependency_overrides[get_eligible_restrooms] = lambda: []

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.7812,
            "start_lon": -73.9665,
            "target_distance_m": 8000.0,
            "restroom_min_mile": 1.0,
            "restroom_max_mile": 2.0,
            "elevation_preference": "flat",
            "shape": shape,
            "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    top = routes[0]
    assert top["matched"] is True
    assert abs(top["distance_m"] - 8000.0) <= 100.0
    # Full response contract is populated for the frontend.
    assert top["restroom"]["facility_name"]
    assert 0.0 <= top["pedestrian_path_ratio"] <= 1.0
    assert "composite_score" in top


def test_local_engine_falls_back_to_ors_on_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")

    def _boom() -> object:
        raise RuntimeError("simulated graph load failure")

    # Force the local engine to fail so the endpoint must fall back.
    monkeypatch.setattr("app.main.get_graph", _boom)

    candidate = RouteCandidate(
        geometry=(
            RoutePoint(lat=40.70, lon=-74.00, elevation_m=0.0),
            RoutePoint(lat=40.71, lon=-74.00, elevation_m=0.0),
            RoutePoint(lat=40.72, lon=-74.00, elevation_m=0.0),
        ),
        distance_m=2220.0,
        elevation_gain_m=0.0,
    )

    class FakeProvider:
        def get_loop(
            self, start: Coordinate, target_distance_m: float, seed: int
        ) -> RouteCandidate:
            return candidate

        def get_route_through_waypoints(
            self, waypoints: list[Coordinate]
        ) -> RouteCandidate:
            return candidate

    restroom = Restroom(
        source_id="t",
        facility_name="On-Route Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.71,  # sits on the candidate geometry
        longitude=-74.00,
    )

    app.dependency_overrides[get_routing_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_eligible_restrooms] = lambda: [restroom]

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

    # Local engine raised -> fell back to ORS -> real result, not a 5xx.
    assert response.status_code == 200, response.text
    assert response.json()
