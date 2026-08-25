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


def test_real_restroom_satisfied_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real restroom placed at a real graph node roughly at the
    requested cumulative distance is genuinely satisfied end to end
    (natural match and/or the constrained round planner -- this test
    doesn't care which, only that the full pipeline wires together)."""
    from app.graph.distances import nearest_node, single_source_paths
    from app.graph.loader import get_graph
    from app.graph.model import node_coordinate
    from app.routing.provider import Coordinate

    monkeypatch.setattr("app.main.get_fountains", lambda: [])

    graph = get_graph()
    start = Coordinate(lat=40.7674, lon=-73.9818)
    target_m = 5 * 1609.34
    start_node = nearest_node(graph, start)
    dists, _paths = single_source_paths(graph, start_node)

    min_range_m, max_range_m = 1 * 1609.34, 4 * 1609.34
    best_node, best_err = None, float("inf")
    target_d = 2.5 * 1609.34
    for node, d in dists.items():
        if min_range_m <= d <= max_range_m:
            err = abs(d - target_d)
            if err < best_err:
                best_err, best_node = err, node
    assert best_node is not None

    coord = node_coordinate(graph, best_node)
    restroom = Restroom(
        source_id="e2e-test",
        facility_name="E2E Test Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=coord.lat,
        longitude=coord.lon,
    )
    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms", lambda client: [restroom]  # noqa: ARG005
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())

    response = client.post(
        "/routes",
        json={
            "start_lat": start.lat,
            "start_lon": start.lon,
            "target_distance_m": target_m,
            "facility_requirements": [
                {
                    "id": "r1",
                    "kind": "restroom",
                    "min_distance_m": min_range_m,
                    "max_distance_m": max_range_m,
                }
            ],
            "shape": "mix",
            "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    assert any(route["constraints_satisfied"] for route in routes)


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


def test_stale_elevation_and_workout_fields_do_not_affect_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Elevation preference and workout type are not mature enough to be
    part of the product yet (see app/main.py's RouteRequest) and must
    never influence /routes generation or ranking. A client still
    sending stale values for them -- e.g. a cached old frontend build --
    must get byte-identical results to a client that omits them
    entirely."""
    monkeypatch.setattr("app.main.get_fountains", lambda: [])
    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms", lambda client: []  # noqa: ARG005
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())

    base_body = {
        **START,
        "target_distance_m": 8000.0,
        "facility_requirements": [],
        "shape": "mix",
        "count": 3,
    }

    baseline = client.post("/routes", json=base_body)
    assert baseline.status_code == 200, baseline.text

    stale_a = client.post(
        "/routes",
        json={
            **base_body,
            "elevation_preference": "flat",
            "workout_type": "tempo",
        },
    )
    stale_b = client.post(
        "/routes",
        json={
            **base_body,
            "elevation_preference": "hilly",
            "workout_type": "easy",
        },
    )

    assert stale_a.status_code == 200, stale_a.text
    assert stale_b.status_code == 200, stale_b.text
    assert stale_a.json() == baseline.json()
    assert stale_b.json() == baseline.json()


def test_route_request_schema_excludes_elevation_and_workout() -> None:
    """The active /routes request model must not expose either
    not-yet-mature input as a real field -- see module docstring."""
    schema = client.get("/openapi.json").json()
    route_request_schema = schema["components"]["schemas"]["RouteRequest"]["properties"]
    assert "elevation_preference" not in route_request_schema
    assert "workout_type" not in route_request_schema


# Regression coverage for the no-facility route-count bug: sector
# starvation in app.generation.turnarounds.select_turnarounds used to cap
# the whole no-facility pipeline at 1-2 candidates on ordinary Manhattan
# requests, even though the product/UI always asks for (and advertises)
# up to 3. Central Park at a moderate distance has plenty of genuine
# turnaround alternatives on the real committed graph, so these are
# reliability regressions, not synthetic best-cases.
_CENTRAL_PARK = {"start_lat": 40.7812, "start_lon": -73.9665}
MILES_TO_METERS = 1609.34


@pytest.mark.parametrize("shape", ["round", "out_and_back", "mix"])
def test_no_facilities_returns_full_requested_count(shape: str) -> None:
    response = client.post(
        "/routes",
        json={
            **_CENTRAL_PARK,
            "target_distance_m": 3.0 * MILES_TO_METERS,
            "facility_requirements": [],
            "shape": shape,
            "count": 3,
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3


def test_no_facilities_mix_includes_both_shapes_when_count_allows() -> None:
    """The engine's mix pool used to rank its combined round/out_and_back
    candidates by roundness before an overcomplete pool ever reached
    final selection -- round loops structurally score higher on
    isoperimetric quotient than a there-and-back line, so that ranking
    silently dropped every out_and_back candidate regardless of pool
    size. This asserts the fix: a real mix request with enough graph
    alternatives returns both shapes, matching the product's mix-shape
    portfolio intent."""
    response = client.post(
        "/routes",
        json={
            **_CENTRAL_PARK,
            "target_distance_m": 3.0 * MILES_TO_METERS,
            "facility_requirements": [],
            "shape": "mix",
            "count": 3,
        },
    )
    assert response.status_code == 200, response.text
    shapes = {route["shape"] for route in response.json()}
    assert shapes == {"round", "out_and_back"}


def test_no_facilities_at_least_one_route_within_tolerance() -> None:
    """Matches the existing benchmark's own success criterion
    (`ScenarioResult.any_within_tolerance` in scripts/benchmark_suite.py):
    at least one returned candidate within +/-100m, not necessarily
    every candidate in a diverse count>1 pool -- `select_diverse`/
    `rank_key` (deliberately unchanged by this fix) already rank
    within-tolerance candidates first and only reach for a
    slightly-off one to fill out `count` once genuinely closer
    alternatives run out."""
    response = client.post(
        "/routes",
        json={
            **_CENTRAL_PARK,
            "target_distance_m": 3.0 * MILES_TO_METERS,
            "facility_requirements": [],
            "shape": "round",
            "count": 3,
        },
    )
    assert response.status_code == 200, response.text
    routes = response.json()
    assert any(route["distance_constraint_satisfied"] for route in routes)
