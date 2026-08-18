"""Regression tests for Supabase outages surfaced via get_eligible_restrooms.

That dependency runs before routing-engine selection (it's a top-level
FastAPI dependency of /routes/with-restroom), so a Supabase outage must
fail as a clean, structured 503 -- not an opaque 500, and not silently
proceed as if restroom data were available.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_interruption_store
from app.flow.interruptions import InterruptionStore
from app.rate_limit import rate_limit_dependency
from app.routing.provider import Coordinate, RouteCandidate


client = TestClient(app)

EMPTY_STORE = InterruptionStore(
    signals=(), crossings=(), signal_cell_index={}, crossing_cell_index={}
)


class RoutingMustNotBeCalledProvider:
    """Spy that fails the test if routing is ever attempted -- proves a
    Supabase outage short-circuits before any routing/ORS work starts."""

    def __init__(self) -> None:
        self.calls = 0

    def get_loop(
        self, start: Coordinate, target_distance_m: float, seed: int
    ) -> RouteCandidate:
        self.calls += 1
        raise AssertionError(
            "routing must not be invoked after a Supabase dependency failure"
        )

    def get_route_through_waypoints(
        self, waypoints: list[Coordinate]
    ) -> RouteCandidate:
        self.calls += 1
        raise AssertionError(
            "routing must not be invoked after a Supabase dependency failure"
        )


@pytest.fixture(autouse=True)
def base_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_interruption_store] = lambda: EMPTY_STORE
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    yield
    app.dependency_overrides.clear()


def _request_body() -> dict[str, object]:
    return {
        "start_lat": 40.70,
        "start_lon": -74.00,
        "target_distance_m": 5000.0,
        "restroom_min_mile": 0.5,
        "restroom_max_mile": 1.5,
        "elevation_preference": "flat",
    }


def test_supabase_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise RuntimeError(
            "simulated Supabase outage: connection refused to "
            "https://secret-project.supabase.co with key sb-abc123"
        )

    monkeypatch.setattr("app.main.get_supabase_client", _boom)
    provider = RoutingMustNotBeCalledProvider()
    monkeypatch.setattr("app.main.get_routing_provider", lambda: provider)

    response = client.post("/routes/with-restroom", json=_request_body())

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Restroom data is temporarily unavailable"
    }


def test_supabase_failure_detail_does_not_leak_exception_internals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_fragment = "sb-abc123-should-not-leak"

    def _boom() -> object:
        raise RuntimeError(
            f"connection refused to https://secret-project.supabase.co "
            f"with key {secret_fragment}"
        )

    monkeypatch.setattr("app.main.get_supabase_client", _boom)
    monkeypatch.setattr(
        "app.main.get_routing_provider",
        lambda: RoutingMustNotBeCalledProvider(),
    )

    response = client.post("/routes/with-restroom", json=_request_body())

    assert response.status_code == 503
    assert secret_fragment not in response.text
    assert "supabase.co" not in response.text.lower()


def test_supabase_failure_does_not_invoke_routing_or_ors_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.main.get_supabase_client",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated outage")),
    )
    provider = RoutingMustNotBeCalledProvider()
    monkeypatch.setattr("app.main.get_routing_provider", lambda: provider)

    response = client.post("/routes/with-restroom", json=_request_body())

    assert response.status_code == 503
    assert provider.calls == 0


def test_supabase_failure_does_not_claim_local_engine_served_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 503 happens before engine selection -- it must never carry
    X-Route-Engine: local, which would falsely claim the local routing
    path produced this response."""
    monkeypatch.setattr("app.main.ROUTING_ENGINE", "local")

    def _boom() -> object:
        raise RuntimeError("simulated outage")

    monkeypatch.setattr("app.main.get_supabase_client", _boom)
    monkeypatch.setattr(
        "app.main.get_routing_provider",
        lambda: RoutingMustNotBeCalledProvider(),
    )

    response = client.post("/routes/with-restroom", json=_request_body())

    assert response.status_code == 503
    assert "X-Route-Engine" not in response.headers
