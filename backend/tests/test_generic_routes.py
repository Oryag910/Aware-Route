"""Endpoint tests for the generic POST /routes contract (PR #18).

Uses the real committed graph artifact (skipped if not built, same
convention as tests/test_local_engine_endpoint.py). Restroom/water
loaders are monkeypatched at the app.main level (not FastAPI
dependency_overrides -- the new endpoint deliberately does NOT fetch
either via an unconditional Depends, see app/main.py's module docstring
for the generic endpoint) so tests can assert a given loader was never
invoked.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.amenities.fountains import Fountain
from app.graph.loader import GRAPH_PATH
from app.main import app
from app.rate_limit import rate_limit_dependency
from app.restrooms.models import Restroom


client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_rate_limit() -> Iterator[None]:
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    yield
    app.dependency_overrides.clear()


def _raise_restrooms() -> list[Restroom]:
    raise AssertionError("restroom loader must not be called")


def _raise_fountains() -> list[Fountain]:
    raise AssertionError("water loader must not be called")


pytestmark = pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)

START = {"start_lat": 40.7812, "start_lon": -73.9665}


def test_no_facilities_never_touches_restroom_or_water_loaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.get_supabase_client", lambda: (_ for _ in ()).throw(
        AssertionError("supabase must not be touched")
    ))
    monkeypatch.setattr("app.main.get_fountains", _raise_fountains)

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [],
            "shape": "mix",
            "count": 2,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    for route in routes:
        assert route["requirements_total"] == 0
        assert route["requirements_satisfied_count"] == 0
        assert route["facility_results"] == []
        assert route["constraints_satisfied"] == route["distance_constraint_satisfied"]


def test_water_only_never_touches_restroom_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.get_supabase_client", lambda: (_ for _ in ()).throw(
        AssertionError("supabase must not be touched for water-only")
    ))
    monkeypatch.setattr("app.main.get_fountains", lambda: [])

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "w1", "kind": "water", "min_distance_m": 1000, "max_distance_m": 3000}
            ],
            "shape": "mix",
            "count": 2,
        },
    )

    assert response.status_code == 200, response.text


def test_restroom_data_outage_returns_503_only_when_restroom_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> object:
        raise RuntimeError("supabase down")

    monkeypatch.setattr("app.main.get_supabase_client", _boom)

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 1000, "max_distance_m": 3000}
            ],
            "shape": "mix",
            "count": 2,
        },
    )

    assert response.status_code == 503, response.text


def test_mixed_requirements_load_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.get_fountains", lambda: [])
    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms", lambda client: []  # noqa: ARG005
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 1000, "max_distance_m": 3000},
                {"id": "w1", "kind": "water", "min_distance_m": 1000, "max_distance_m": 3000},
            ],
            "shape": "mix",
            "count": 2,
        },
    )

    assert response.status_code == 200, response.text
    top = response.json()[0]
    assert top["requirements_total"] == 2
    requirement_ids = {r["requirement_id"] for r in top["facility_results"]}
    assert requirement_ids == {"r1", "w1"}


def test_response_requirement_order_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.get_fountains", lambda: [])

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "w2", "kind": "water", "min_distance_m": 5000, "max_distance_m": 7000},
                {"id": "w1", "kind": "water", "min_distance_m": 1000, "max_distance_m": 3000},
            ],
            "shape": "mix",
            "count": 1,
        },
    )

    assert response.status_code == 200, response.text
    top = response.json()[0]
    ids_in_order = [r["requirement_id"] for r in top["facility_results"]]
    assert ids_in_order == ["w2", "w1"]


def test_duplicate_requirement_ids_rejected() -> None:
    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 0, "max_distance_m": 1000},
                {"id": "r1", "kind": "water", "min_distance_m": 0, "max_distance_m": 1000},
            ],
        },
    )
    assert response.status_code == 422


def test_max_greater_than_target_rejected() -> None:
    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 0, "max_distance_m": 9000}
            ],
        },
    )
    assert response.status_code == 422


def test_min_greater_equal_max_rejected() -> None:
    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 2000, "max_distance_m": 1000}
            ],
        },
    )
    assert response.status_code == 422


def test_variable_length_six_requirements_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.get_fountains", lambda: [])
    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms", lambda client: []  # noqa: ARG005
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())

    requirements = [
        {"id": f"r{i}", "kind": "restroom", "min_distance_m": i * 500, "max_distance_m": i * 500 + 400}
        for i in range(1, 4)
    ] + [
        {"id": f"w{i}", "kind": "water", "min_distance_m": i * 500, "max_distance_m": i * 500 + 400}
        for i in range(1, 4)
    ]

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 10000.0,
            "facility_requirements": requirements,
            "shape": "mix",
            "count": 1,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()[0]["requirements_total"] == 6


def test_x_route_engine_header_present() -> None:
    response = client.post(
        "/routes",
        json={**START, "target_distance_m": 8000.0, "facility_requirements": []},
    )
    assert response.status_code == 200
    assert response.headers["X-Route-Engine"] == "local"


def test_no_json_infinity_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsatisfiable requirement (no compatible facility exists at
    all) must serialize range_error_m as null, not Infinity."""
    monkeypatch.setattr("app.main.get_fountains", lambda: [])

    response = client.post(
        "/routes",
        json={
            **START,
            "target_distance_m": 8000.0,
            "facility_requirements": [
                {"id": "w1", "kind": "water", "min_distance_m": 0, "max_distance_m": 100}
            ],
            "count": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert "Infinity" not in response.text
