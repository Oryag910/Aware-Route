"""Endpoint tests for ROUTING_ENGINE=local and its ORS fallback.

The success test exercises the real committed graph artifact + real
fountains (restrooms overridden empty, so no Supabase); it's skipped if
the artifact isn't present. The fallback test needs no graph -- it forces
a local-engine failure and asserts the request still succeeds via ORS.

Also covers X-Route-Engine header provenance: it must read "local" on a
local success AND on a local-generated 422 (both are the local engine
truthfully reporting what served the request), and "ors" only when the
local engine actually failed and ORS genuinely ran.
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
    request_body = {
        "start_lat": 40.7812,
        "start_lon": -73.9665,
        "target_distance_m": 8000.0,
        "restroom_min_mile": 1.0,
        "restroom_max_mile": 2.0,
        "elevation_preference": "flat",
        "shape": shape,
        "count": 3,
    }

    # Fountains-only amenity pool (no Supabase needed) -- a fountain
    # alone must NOT satisfy the restroom requirement, so this is an
    # unmatched "closest available" fallback, not a match.
    app.dependency_overrides[get_eligible_restrooms] = lambda: []
    baseline = client.post("/routes/with-restroom", json=request_body)
    assert baseline.status_code == 200, baseline.text
    baseline_top = baseline.json()[0]
    assert baseline_top["matched"] is False
    assert baseline_top["restroom"]["kind"] == "fountain"

    # Now place a real restroom at that exact on-route location -- proves
    # the real graph + generation + scoring pipeline can produce a
    # genuine restroom match end to end, without depending on where
    # real-world restrooms happen to sit relative to this graph.
    restroom = Restroom(
        source_id="test-restroom",
        facility_name="Test Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=baseline_top["restroom"]["latitude"],
        longitude=baseline_top["restroom"]["longitude"],
    )
    app.dependency_overrides[get_eligible_restrooms] = lambda: [restroom]

    response = client.post("/routes/with-restroom", json=request_body)

    assert response.status_code == 200, response.text
    assert response.headers["X-Route-Engine"] == "local"
    routes = response.json()
    assert routes
    top = routes[0]
    assert top["matched"] is True
    assert top["restroom"]["kind"] == "restroom"
    assert abs(top["distance_m"] - 8000.0) <= 100.0
    # Full response contract is populated for the frontend.
    assert top["restroom"]["facility_name"]
    assert 0.0 <= top["pedestrian_path_ratio"] <= 1.0
    assert "composite_score" in top


@pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)
def test_local_engine_422_does_not_fall_back_to_ors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An honest local no-match (no restroom, no fountain, in range on
    any candidate) is a 422 straight from the local engine -- it must
    NOT fall back to ORS, and the header must still truthfully say
    "local" served the request (it did -- it just found nothing)."""
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")
    # No restrooms and no fountains -> zero amenities anywhere -> every
    # candidate is guaranteed unmatched, deterministically (not relying
    # on real-world fountain geography happening to miss this start).
    app.dependency_overrides[get_eligible_restrooms] = lambda: []
    monkeypatch.setattr("app.main.get_fountains", lambda: [])

    class RoutingMustNotBeCalledProvider:
        def get_loop(
            self, start: Coordinate, target_distance_m: float, seed: int
        ) -> RouteCandidate:
            raise AssertionError("ORS must not be called on a local 422")

        def get_route_through_waypoints(
            self, waypoints: list[Coordinate]
        ) -> RouteCandidate:
            raise AssertionError("ORS must not be called on a local 422")

    monkeypatch.setattr(
        "app.main.get_routing_provider",
        lambda: RoutingMustNotBeCalledProvider(),
    )

    response = client.post(
        "/routes/with-restroom",
        json={
            "start_lat": 40.7812,
            "start_lon": -73.9665,
            "target_distance_m": 8000.0,
            "restroom_min_mile": 1.0,
            "restroom_max_mile": 2.0,
            "elevation_preference": "flat",
            "shape": "mix",
            "count": 3,
        },
    )

    assert response.status_code == 422, response.text
    assert response.headers["X-Route-Engine"] == "local"


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
        def __init__(self) -> None:
            self.calls = 0

        def get_loop(
            self, start: Coordinate, target_distance_m: float, seed: int
        ) -> RouteCandidate:
            self.calls += 1
            return candidate

        def get_route_through_waypoints(
            self, waypoints: list[Coordinate]
        ) -> RouteCandidate:
            self.calls += 1
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

    fake_provider = FakeProvider()
    monkeypatch.setattr(
        "app.main.get_routing_provider", lambda: fake_provider
    )
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
    assert response.headers["X-Route-Engine"] == "ors"
    assert response.json()
    # Prove ORS was genuinely invoked, not just that the response happened
    # to succeed.
    assert fake_provider.calls > 0
