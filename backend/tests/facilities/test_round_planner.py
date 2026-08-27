from unittest.mock import patch

import networkx as nx
import pytest

import app.facilities.round_planner as round_planner_module
from app.facilities.models import Facility, FacilityRequirement
from app.facilities.planning_deadline import PlanningDeadline
from app.facilities.round_planner import (
    FULL_BUILD_BUDGET,
    _adaptive_build_budget,
    plan_constrained_round,
)
from app.facilities.encounters import find_facility_encounters
from app.facilities.assignment import assign_requirements
from app.generation.polygon_loop import MAX_EDGE_REUSE_RATIO
from app.generation.quality import edge_reuse_ratio
from app.routing.geometry import destination_point, haversine_m
from app.routing.provider import Coordinate
from scripts.benchmark_suite import _short_start_return_spur


# Same style of dense synthetic grid used by tests/generation/test_polygon_amenity.py
# -- large enough to contain every TEMPLATES rectangle at TARGET_DISTANCE_M
# with margin, dense enough for reuse-penalized legs to have real
# alternate streets.
GRID_N = 41
GRID_SPACING_M = 50.0
GRID_ORIGIN = Coordinate(lat=40.750, lon=-73.980)
GRID_CENTER_INDEX = GRID_N // 2

TARGET_DISTANCE_M = 2400.0
COUNT = 3


def _node_id(i: int, j: int) -> int:
    return i * GRID_N + j


def _node_coord(i: int, j: int) -> Coordinate:
    north = destination_point(GRID_ORIGIN, 0.0, i * GRID_SPACING_M)
    return destination_point(north, 90.0, j * GRID_SPACING_M)


@pytest.fixture(scope="module")
def grid_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(crs="epsg:4326")

    for i in range(GRID_N):
        for j in range(GRID_N):
            coord = _node_coord(i, j)
            graph.add_node(_node_id(i, j), x=coord.lon, y=coord.lat)

    for i in range(GRID_N):
        for j in range(GRID_N):
            u = _node_id(i, j)
            u_coord = _node_coord(i, j)
            if i + 1 < GRID_N:
                v = _node_id(i + 1, j)
                length = haversine_m(u_coord, _node_coord(i + 1, j))
                graph.add_edge(u, v, key=0, length=length)
                graph.add_edge(v, u, key=0, length=length)
            if j + 1 < GRID_N:
                v = _node_id(i, j + 1)
                length = haversine_m(u_coord, _node_coord(i, j + 1))
                graph.add_edge(u, v, key=0, length=length)
                graph.add_edge(v, u, key=0, length=length)

    return graph


@pytest.fixture(scope="module")
def start(grid_graph: nx.MultiDiGraph) -> Coordinate:
    return _node_coord(GRID_CENTER_INDEX, GRID_CENTER_INDEX)


def _facility(i: int, j: int, kind: str, facility_id: str) -> Facility:
    coord = _node_coord(i, j)
    return Facility(
        id=facility_id,
        kind=kind,  # type: ignore[arg-type]
        lat=coord.lat,
        lon=coord.lon,
        name=facility_id,
        status=None,
        hours_of_operation=None,
        source="test",
    )


# Plausible on-one-leg positions of a broad loop around the grid center
# (mirrors test_polygon_amenity.py's RESTROOM_OFFSET/FOUNTAIN_OFFSET).
RESTROOM_A = (GRID_CENTER_INDEX + 8, GRID_CENTER_INDEX + 6)
RESTROOM_B = (GRID_CENTER_INDEX - 2, GRID_CENTER_INDEX + 9)
WATER_A = (GRID_CENTER_INDEX + 7, GRID_CENTER_INDEX - 6)
WATER_B = (GRID_CENTER_INDEX - 8, GRID_CENTER_INDEX - 4)

MIN_RANGE_M = 0.25 * TARGET_DISTANCE_M
MAX_RANGE_M = 0.75 * TARGET_DISTANCE_M


def _requirement(req_id: str, kind: str) -> FacilityRequirement:
    return FacilityRequirement(
        id=req_id, kind=kind, min_distance_m=MIN_RANGE_M, max_distance_m=MAX_RANGE_M  # type: ignore[arg-type]
    )


def test_wrong_shape_returns_empty(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    facility = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]
    assert (
        plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "out_and_back", COUNT, reqs, [facility])
        == []
    )


def test_no_requirements_returns_empty(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    facility = _facility(*RESTROOM_A, "restroom", "restroom:a")
    assert plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, [], [facility]) == []


def test_two_fixed_facilities_both_reachable(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    water = _facility(*WATER_A, "water", "water:a")
    reqs = [_requirement("r1", "restroom"), _requirement("w1", "water")]

    routes = plan_constrained_round(
        grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom, water]
    )

    assert routes
    top = routes[0]
    encounters = find_facility_encounters(top.candidate.geometry, [restroom, water])
    results = assign_requirements(reqs, encounters)
    assert all(r.satisfied for r in results)


def test_mixed_restroom_and_water(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    facilities = [
        _facility(*RESTROOM_A, "restroom", "restroom:a"),
        _facility(*WATER_A, "water", "water:a"),
    ]
    reqs = [_requirement("r1", "restroom"), _requirement("w1", "water")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "mix", COUNT, reqs, facilities)
    assert routes


def test_multiple_facilities_same_leg(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    # Two restrooms placed close together, on what should be the same
    # target leg for most templates (both clustered near RESTROOM_A).
    facilities = [
        _facility(GRID_CENTER_INDEX + 8, GRID_CENTER_INDEX + 6, "restroom", "restroom:a"),
        _facility(GRID_CENTER_INDEX + 8, GRID_CENTER_INDEX + 7, "restroom", "restroom:b"),
    ]
    reqs = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0.2 * TARGET_DISTANCE_M, max_distance_m=0.5 * TARGET_DISTANCE_M),
        FacilityRequirement(id="r2", kind="restroom", min_distance_m=0.4 * TARGET_DISTANCE_M, max_distance_m=0.75 * TARGET_DISTANCE_M),
    ]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, facilities)
    assert routes


def test_four_facilities(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    facilities = [
        _facility(*RESTROOM_A, "restroom", "restroom:a"),
        _facility(*RESTROOM_B, "restroom", "restroom:b"),
        _facility(*WATER_A, "water", "water:a"),
        _facility(*WATER_B, "water", "water:b"),
    ]
    reqs = [
        _requirement("r1", "restroom"),
        _requirement("r2", "restroom"),
        _requirement("w1", "water"),
        _requirement("w2", "water"),
    ]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, facilities)
    assert routes
    top = routes[0]
    encounters = find_facility_encounters(top.candidate.geometry, facilities)
    results = assign_requirements(reqs, encounters)
    satisfied = sum(1 for r in results if r.satisfied)
    assert satisfied >= 1  # at least natural/planned overlap; not asserting all 4 on synthetic grid


def test_six_plus_facilities_bounded_and_succeeds(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    offsets = [
        (GRID_CENTER_INDEX + 8, GRID_CENTER_INDEX + 6),
        (GRID_CENTER_INDEX - 2, GRID_CENTER_INDEX + 9),
        (GRID_CENTER_INDEX + 7, GRID_CENTER_INDEX - 6),
        (GRID_CENTER_INDEX - 8, GRID_CENTER_INDEX - 4),
        (GRID_CENTER_INDEX + 3, GRID_CENTER_INDEX + 8),
        (GRID_CENTER_INDEX - 6, GRID_CENTER_INDEX + 2),
    ]
    kinds = ["restroom", "water", "restroom", "water", "restroom", "water"]
    facilities = [
        _facility(i, j, kind, f"{kind}:{index}")
        for index, ((i, j), kind) in enumerate(zip(offsets, kinds))
    ]
    reqs = [
        FacilityRequirement(
            id=f"req{index}",
            kind=kind,  # type: ignore[arg-type]
            min_distance_m=0.1 * TARGET_DISTANCE_M,
            max_distance_m=0.9 * TARGET_DISTANCE_M,
        )
        for index, kind in enumerate(kinds)
    ]

    with patch(
        "app.facilities.round_planner._tune_waypoints",
        wraps=__import__("app.facilities.round_planner", fromlist=["_tune_waypoints"])._tune_waypoints,
    ) as wrapped:
        routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, facilities)
        # Bounded: real graph builds (one _tune_waypoints call per
        # attempted plan) never exceed the global full-build budget,
        # regardless of 6 requirements x several candidate facilities each.
        assert wrapped.call_count <= FULL_BUILD_BUDGET

    assert routes


def test_final_markers_in_requested_ranges(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom])
    assert routes

    top = routes[0]
    encounters = find_facility_encounters(top.candidate.geometry, [restroom])
    results = assign_requirements(reqs, encounters)
    assert results[0].satisfied
    assert results[0].encounter is not None
    assert MIN_RANGE_M <= results[0].encounter.mile_marker_m <= MAX_RANGE_M


def test_final_distance_near_target(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom])
    assert routes
    for route in routes:
        assert abs(route.candidate.distance_m - TARGET_DISTANCE_M) <= 0.35 * TARGET_DISTANCE_M


def test_no_start_return_spur(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom])
    assert routes
    for route in routes:
        assert not _short_start_return_spur(route.candidate.geometry)


def test_route_reaches_back_to_start_no_disconnection(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom])
    assert routes
    for route in routes:
        assert len(route.candidate.geometry) >= 2
        assert route.node_path[0] == route.node_path[-1]


def test_edge_reuse_reasonable(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom])
    assert routes
    for route in routes:
        assert edge_reuse_ratio(route.node_path) <= MAX_EDGE_REUSE_RATIO


def test_deterministic(grid_graph: nx.MultiDiGraph, start: Coordinate) -> None:
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    water = _facility(*WATER_A, "water", "water:a")
    reqs = [_requirement("r1", "restroom"), _requirement("w1", "water")]

    first = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom, water])
    second = plan_constrained_round(grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom, water])

    first_paths = [r.node_path for r in first]
    second_paths = [r.node_path for r in second]
    assert first_paths == second_paths


# --- Requirement-adaptive build budget & cooperative deadline -----------


def test_adaptive_build_budget_by_requirement_count() -> None:
    assert _adaptive_build_budget(1) == FULL_BUILD_BUDGET
    assert _adaptive_build_budget(2) == 4
    assert _adaptive_build_budget(3) == 3
    assert _adaptive_build_budget(6) == 3


def test_already_expired_deadline_returns_no_candidates(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """An already-expired deadline must stop the planner before any real
    graph build -- it never raises, just returns whatever (nothing) was
    built so far."""
    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]

    routes = plan_constrained_round(
        grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom],
        deadline=PlanningDeadline(budget_s=-1.0),
    )
    assert routes == []


def test_deadline_expiring_mid_search_keeps_partial_candidates(
    grid_graph: nx.MultiDiGraph, start: Coordinate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the deadline expires right after one useful build completes,
    that candidate must still be returned rather than discarded. Ties
    expiry to a successful build (not a raw call count) so the test
    doesn't depend on how many build attempts internally fail first."""
    import app.facilities.round_planner as round_planner_module

    build_count = {"n": 0}
    orig_build_plan = round_planner_module._build_plan

    def counted_build_plan(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        result = orig_build_plan(*args, **kwargs)  # type: ignore[arg-type]
        if result is not None:
            build_count["n"] += 1
        return result

    monkeypatch.setattr(round_planner_module, "_build_plan", counted_build_plan)

    class _ExpireAfterOneBuild(PlanningDeadline):
        def __init__(self) -> None:
            super().__init__(budget_s=9999.0)

        def expired(self) -> bool:
            return build_count["n"] >= 1

    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    water = _facility(*WATER_A, "water", "water:a")
    reqs = [_requirement("r1", "restroom"), _requirement("w1", "water")]

    routes = plan_constrained_round(
        grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom, water],
        deadline=_ExpireAfterOneBuild(),
    )
    assert len(routes) == 1


def test_build_plan_shares_one_deadline_with_calibration_and_tuning(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """The SAME deadline object given to `plan_constrained_round` must be
    the one `_affine_calibration_scale_via` and `_tune_waypoints`
    cooperatively check -- not an independent, freshly-created deadline
    inside `_build_plan`."""
    captured: dict[str, object] = {}
    orig_affine = round_planner_module._affine_calibration_scale_via  # type: ignore[attr-defined]
    orig_tune = round_planner_module._tune_waypoints  # type: ignore[attr-defined]

    def spy_affine(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        captured["affine_should_continue"] = kwargs.get("should_continue")
        return orig_affine(*args, **kwargs)  # type: ignore[arg-type]

    def spy_tune(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        captured["tune_should_continue"] = kwargs.get("should_continue")
        return orig_tune(*args, **kwargs)  # type: ignore[arg-type]

    restroom = _facility(*RESTROOM_A, "restroom", "restroom:a")
    reqs = [_requirement("r1", "restroom")]
    deadline = PlanningDeadline(budget_s=9999.0)

    with (
        patch.object(round_planner_module, "_affine_calibration_scale_via", spy_affine),
        patch.object(round_planner_module, "_tune_waypoints", spy_tune),
    ):
        routes = plan_constrained_round(
            grid_graph, start, TARGET_DISTANCE_M, "round", COUNT, reqs, [restroom],
            deadline=deadline,
        )

    assert routes
    assert captured["affine_should_continue"] is not None
    assert captured["tune_should_continue"] is not None
    # Both callbacks must reflect the SAME underlying deadline object --
    # marking it expired makes both report "stop" simultaneously.
    assert captured["affine_should_continue"]() is True  # type: ignore[operator]
    assert captured["tune_should_continue"]() is True  # type: ignore[operator]
    deadline.budget_s = -1.0
    assert captured["affine_should_continue"]() is False  # type: ignore[operator]
    assert captured["tune_should_continue"]() is False  # type: ignore[operator]
