"""Canonical PR #18 product scenarios (spec section 31), automated as
integration tests against the real committed Manhattan graph.

Facility fixtures are deterministic and injected (never live Supabase --
see `app.facilities.catalog`'s conditional loading, exercised via
monkeypatch here exactly like `tests/test_generic_routes.py`). Each
fixture is placed at the EXACT coordinate a real reference route (a
natural, unconstrained candidate for the same start/distance/shape)
passes at the requested window's midpoint mile marker -- this is
STRATUM A ("feasible by construction") from spec section 32: it proves
the mechanism CAN honor the requested cumulative-mile stop (the
reference route itself is proof a route through that exact point at
that exact cumulative distance exists), without depending on where
real-world facilities happen to sit. An earlier version of this test
placed facilities by straight shortest-path distance from start, which
is only a correct proxy for an out-and-back's outbound leg -- a round
loop's cumulative position at a given fraction is usually much CLOSER
to start (as the crow flies) than that fraction of the total distance,
since a loop curves back. Deriving from a real route's own geometry
sidesteps needing to reason about that by construction.

These tests report actual measured satisfaction (`print`ed) rather than
require 100% -- distance-tuning and beam-search placement across
MULTIPLE simultaneous fixed waypoints on a real, non-uniform street
graph is inherently approximate (see `app/facilities/round_planner.py`
and `app/facilities/oab_planner.py` module docstrings). What IS
asserted strictly: a 200 response, correct `requirements_total`, exact
kind matching, and -- for Scenario D -- that reversing the request's
requirement order produces an equivalent per-requirement outcome (a
hard correctness property of the deterministic assignment engine, not
a heuristic search result).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.generation.engine import generate_routes
from app.graph.loader import GRAPH_PATH, get_graph
from app.main import app
from app.rate_limit import rate_limit_dependency
from app.restrooms.geo import cumulative_distances_m
from app.restrooms.models import Restroom
from app.routing.provider import Coordinate, RoutePoint


client = TestClient(app)
pytestmark = pytest.mark.skipif(
    not GRAPH_PATH.exists(), reason="graph artifact not built"
)

MI = 1609.34
START = Coordinate(lat=40.7674, lon=-73.9818)


@pytest.fixture(autouse=True)
def _no_rate_limit() -> Iterator[None]:
    app.dependency_overrides[rate_limit_dependency] = lambda: None
    yield
    app.dependency_overrides.clear()


def _reference_geometry(target_m: float, shape: str = "round") -> tuple[RoutePoint, ...]:
    graph = get_graph()
    routes = generate_routes(graph, START, target_m, shape, 1)  # type: ignore[arg-type]
    assert routes, "no reference route available near this start/distance"
    return routes[0].candidate.geometry


def _point_at_mile(geometry: tuple[RoutePoint, ...], target_m: float) -> RoutePoint:
    distances = cumulative_distances_m(geometry)
    best_index, best_err = 0, float("inf")
    for index, distance in enumerate(distances):
        err = abs(distance - target_m)
        if err < best_err:
            best_err, best_index = err, index
    return geometry[best_index]


def _restroom_at(point: RoutePoint, source_id: str) -> Restroom:
    return Restroom(
        source_id=source_id,
        facility_name=f"Test Restroom {source_id}",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=point.lat,
        longitude=point.lon,
    )


def test_scenario_b_12mi_no_facilities(monkeypatch: pytest.MonkeyPatch) -> None:
    """12mi, zero facility requirements: ordinary baseline behavior."""
    monkeypatch.setattr(
        "app.main.get_supabase_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("no-facility request must not touch Supabase")
        ),
    )
    monkeypatch.setattr(
        "app.main.get_fountains",
        lambda: (_ for _ in ()).throw(
            AssertionError("no-facility request must not touch the water dataset")
        ),
    )

    response = client.post(
        "/routes",
        json={
            "start_lat": START.lat, "start_lon": START.lon,
            "target_distance_m": 12 * MI,
            "facility_requirements": [],
            "shape": "mix", "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    for route in routes:
        assert route["requirements_total"] == 0
        assert route["constraints_satisfied"] == route["distance_constraint_satisfied"]
    assert any(abs(r["distance_error_m"]) <= 100.0 for r in routes)


def test_scenario_c_5mi_water_3_4mi(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.amenities.fountains import Fountain

    target_m = 5 * MI
    reference = _reference_geometry(target_m, "out_and_back")
    point = _point_at_mile(reference, 3.5 * MI)

    monkeypatch.setattr("app.main.get_fountains", lambda: [])
    monkeypatch.setattr(
        "app.main._water_loader",
        lambda: [Fountain(osm_id=1, latitude=point.lat, longitude=point.lon, name="Test Water")],
    )

    response = client.post(
        "/routes",
        json={
            "start_lat": START.lat, "start_lon": START.lon,
            "target_distance_m": target_m,
            "facility_requirements": [
                {"id": "w1", "kind": "water", "min_distance_m": 3 * MI, "max_distance_m": 4 * MI}
            ],
            "shape": "mix", "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    assert routes[0]["requirements_total"] == 1
    print(f"Scenario C satisfied: {routes[0]['requirements_satisfied_count']}/1")
    assert routes[0]["requirements_satisfied_count"] == 1, (
        "reference out-and-back geometry passes exactly at this point/mile -- "
        "the natural-match pool alone should find it"
    )


def test_scenario_a_10mi_two_restroom_two_water(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.amenities.fountains import Fountain

    target_m = 10 * MI
    reference = _reference_geometry(target_m, "round")

    r1_point = _point_at_mile(reference, 3 * MI)
    r2_point = _point_at_mile(reference, 7.5 * MI)
    w1_point = _point_at_mile(reference, 4 * MI)
    w2_point = _point_at_mile(reference, 7 * MI)

    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms",
        lambda client: [  # noqa: ARG005
            _restroom_at(r1_point, "a-r1"),
            _restroom_at(r2_point, "a-r2"),
        ],
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())
    monkeypatch.setattr(
        "app.main._water_loader",
        lambda: [
            Fountain(osm_id=1, latitude=w1_point.lat, longitude=w1_point.lon, name="w1"),
            Fountain(osm_id=2, latitude=w2_point.lat, longitude=w2_point.lon, name="w2"),
        ],
    )

    response = client.post(
        "/routes",
        json={
            "start_lat": START.lat, "start_lon": START.lon,
            "target_distance_m": target_m,
            "facility_requirements": [
                {"id": "r1", "kind": "restroom", "min_distance_m": 2 * MI, "max_distance_m": 4 * MI},
                {"id": "r2", "kind": "restroom", "min_distance_m": 6 * MI, "max_distance_m": 9 * MI},
                {"id": "w1", "kind": "water", "min_distance_m": 3 * MI, "max_distance_m": 5 * MI},
                {"id": "w2", "kind": "water", "min_distance_m": 6 * MI, "max_distance_m": 8 * MI},
            ],
            "shape": "mix", "count": 3,
        },
    )

    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    top = routes[0]
    assert top["requirements_total"] == 4
    assert {r["requirement_id"] for r in top["facility_results"]} == {"r1", "r2", "w1", "w2"}
    expected_kind = {"r1": "restroom", "r2": "restroom", "w1": "water", "w2": "water"}
    for result in top["facility_results"]:
        assert result["kind"] == expected_kind[result["requirement_id"]]
        if result["facility"] is not None:
            assert result["facility"]["kind"] == expected_kind[result["requirement_id"]]
    print(f"Scenario A satisfied: {top['requirements_satisfied_count']}/4")
    # The reference route itself satisfies all 4 by construction; the
    # natural-match pool should recover at least that route (or an
    # equally good one).
    assert top["requirements_satisfied_count"] == 4


@pytest.mark.parametrize("reversed_order", [False, True])
def test_scenario_d_8mi_two_restrooms_order_invariant(
    monkeypatch: pytest.MonkeyPatch, reversed_order: bool
) -> None:
    target_m = 8 * MI
    reference = _reference_geometry(target_m, "round")
    d1_point = _point_at_mile(reference, 5.5 * MI)
    d2_point = _point_at_mile(reference, 2.5 * MI)

    monkeypatch.setattr(
        "app.main.fetch_eligible_restrooms",
        lambda client: [_restroom_at(d1_point, "d-1"), _restroom_at(d2_point, "d-2")],  # noqa: ARG005
    )
    monkeypatch.setattr("app.main.get_supabase_client", lambda: object())

    requirements = [
        {"id": "r1", "kind": "restroom", "min_distance_m": 5 * MI, "max_distance_m": 6 * MI},
        {"id": "r2", "kind": "restroom", "min_distance_m": 2 * MI, "max_distance_m": 3 * MI},
    ]
    if reversed_order:
        requirements = list(reversed(requirements))

    response = client.post(
        "/routes",
        json={
            "start_lat": START.lat, "start_lon": START.lon,
            "target_distance_m": target_m,
            "facility_requirements": requirements,
            "shape": "mix", "count": 3,
        },
    )
    assert response.status_code == 200, response.text
    routes = response.json()
    assert routes
    top = routes[0]
    assert top["requirements_satisfied_count"] == 2

    outcome = {
        r["requirement_id"]: (
            r["satisfied"],
            r["facility"]["id"] if r["facility"] else None,
            r["facility"]["encounter_index"] if r["facility"] else None,
        )
        for r in top["facility_results"]
    }
    facility_ids = {v[1] for v in outcome.values()}
    if len(facility_ids) == 1:
        # Both requirements satisfied by the SAME physical facility --
        # only legitimate if they're two DISTINCT encounters (e.g. an
        # out-and-back genuinely passing it outbound and on return).
        encounter_indices = {v[2] for v in outcome.values()}
        assert len(encounter_indices) == 2, (
            "one encounter must not satisfy two requirements"
        )

    key = "_scenario_d_outcome"
    previous = getattr(test_scenario_d_8mi_two_restrooms_order_invariant, key, None)
    setattr(test_scenario_d_8mi_two_restrooms_order_invariant, key, outcome)
    if previous is not None:
        assert previous == outcome, (
            "requirement order must not change the per-requirement outcome"
        )
    print(f"Scenario D (reversed={reversed_order}) outcome: {outcome}")
