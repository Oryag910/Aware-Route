"""Tests for the bounded, deterministic constrained out-and-back planner.

Real-graph tests use the committed graph artifact the same way
`tests/test_local_engine_endpoint.py` does -- skipped if it isn't built.
"""

import ast
import pathlib
import random
from unittest.mock import MagicMock

import pytest

import app.facilities.oab_planner as oab_planner
from app.facilities.assignment import assign_requirements
from app.facilities.encounters import find_facility_encounters
from app.facilities.models import Facility, FacilityRequirement
from app.facilities.oab_planner import _adaptive_build_budget, plan_constrained_out_and_back
from app.facilities.planning_deadline import PlanningDeadline
from app.graph.distances import nearest_node, single_source_paths
from app.graph.loader import GRAPH_PATH, get_graph
from app.graph.model import node_coordinate
from app.routing.geometry import bearing_deg
from app.routing.provider import Coordinate


pytestmark_graph = pytest.mark.skipif(
    not GRAPH_PATH.exists(), reason="graph artifact not built"
)

START = Coordinate(lat=40.7812, lon=-73.9665)


# --- Cheap unit tests (no graph needed) --------------------------------


def test_shape_round_returns_empty_without_touching_graph() -> None:
    graph = MagicMock()
    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    fac = Facility(
        id="restroom:1", kind="restroom", lat=40.0, lon=-73.0,
        name=None, status=None, hours_of_operation=None, source="test",
    )
    result = plan_constrained_out_and_back(
        graph, START, 8000.0, "round", 3, [req], [fac]
    )
    assert result == []
    graph.assert_not_called()
    graph.nodes.__getitem__.assert_not_called()


def test_no_requirements_returns_empty_without_touching_graph() -> None:
    graph = MagicMock()
    result = plan_constrained_out_and_back(
        graph, START, 8000.0, "out_and_back", 3, [], []
    )
    assert result == []
    graph.assert_not_called()
    graph.nodes.__getitem__.assert_not_called()


def test_no_length_tune_import() -> None:
    """No spur padding: `length_tune` must never be IMPORTED by this
    module -- a static AST check of actual import statements (the module
    docstring may legitimately mention `length_tune.py` by name to
    document that it's forbidden, so a plain substring check over the
    whole source is too strict; import-time detection via sys.modules is
    fragile the other way -- another module may import it first)."""
    source = pathlib.Path(oab_planner.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "length_tune" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or "length_tune" not in node.module


# --- Real-graph helpers --------------------------------------------------


def _node_near_bearing(
    graph: object,
    start_coord: Coordinate,
    dists: dict[int, float],
    target_d: float,
    target_bearing: float,
    tol_d: float = 500.0,
    tol_b: float = 45.0,
) -> int | None:
    """Find a real graph node at roughly `target_d` distance from start
    and roughly `target_bearing` from start, so synthetic test facilities
    land at distinct, spatially coherent positions (rather than all
    colliding on the single globally-nearest node for a given distance)."""
    best_node: int | None = None
    best_err = float("inf")
    for node, d in dists.items():
        if abs(d - target_d) > tol_d:
            continue
        bearing = bearing_deg(start_coord, node_coordinate(graph, node))
        gap = min(abs(bearing - target_bearing), 360.0 - abs(bearing - target_bearing))
        if gap > tol_b:
            continue
        err = abs(d - target_d)
        if err < best_err:
            best_err, best_node = err, node
    return best_node


def _facility_at(
    graph: object, node: int, kind: str, facility_id: str
) -> Facility:
    coord = node_coordinate(graph, node)
    return Facility(
        id=facility_id, kind=kind, lat=coord.lat, lon=coord.lon,  # type: ignore[arg-type]
        name=None, status=None, hours_of_operation=None, source="test",
    )


# --- Real-graph tests ----------------------------------------------------


@pytestmark_graph
def test_outbound_half_requirement_is_satisfiable() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    radial = target_m / 6.0  # squarely in the first third
    node = _node_near_bearing(graph, start_coord, dists, radial, 0.0, tol_b=180.0)
    assert node is not None, "graph topology near the test start point changed"

    req = FacilityRequirement(
        id="r1", kind="restroom",
        min_distance_m=dists[node] - 400, max_distance_m=dists[node] + 400,
    )
    fac = _facility_at(graph, node, "restroom", "restroom:1")

    routes = plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, [req], [fac])
    assert routes, "planner produced no candidates"

    satisfied_any = False
    for route in routes:
        encounters = find_facility_encounters(route.candidate.geometry, [fac])
        results = assign_requirements([req], encounters)
        if all(r.satisfied for r in results):
            satisfied_any = True
            break
    assert satisfied_any, "no returned candidate actually satisfies the requirement"


@pytestmark_graph
def test_return_half_requirement_is_satisfiable() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    # Radial position near target/2 (straddles the turnaround) so the
    # requirement's window covers the RETURN half's mile marker
    # (target - radial), not the outbound approach.
    radial = target_m / 2.0 - 300.0
    node = _node_near_bearing(graph, start_coord, dists, radial, 0.0, tol_b=180.0)
    assert node is not None, "graph topology near the test start point changed"

    return_mile_marker = target_m - dists[node]
    req = FacilityRequirement(
        id="r1", kind="water",
        min_distance_m=return_mile_marker - 500, max_distance_m=return_mile_marker + 500,
    )
    fac = _facility_at(graph, node, "water", "water:1")

    routes = plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, [req], [fac])
    assert routes, "planner produced no candidates"

    satisfied_any = False
    for route in routes:
        encounters = find_facility_encounters(route.candidate.geometry, [fac])
        results = assign_requirements([req], encounters)
        if all(r.satisfied for r in results):
            satisfied_any = True
            break
    assert satisfied_any, "no returned candidate actually satisfies the requirement"


def _build_mixed_requirements(
    graph: object, start_coord: Coordinate, dists: dict[int, float], target_m: float, n: int
) -> tuple[list[FacilityRequirement], list[Facility]]:
    reqs: list[FacilityRequirement] = []
    facs: list[Facility] = []
    for i in range(n):
        kind = "restroom" if i % 2 == 0 else "water"
        radial = (target_m / 2.0) * (i + 1) / (n + 1)
        node = _node_near_bearing(graph, start_coord, dists, radial, 200.0)
        assert node is not None, "graph topology near the test start point changed"
        req = FacilityRequirement(
            id=f"r{i}", kind=kind,  # type: ignore[arg-type]
            min_distance_m=radial - 600, max_distance_m=radial + 600,
        )
        reqs.append(req)
        facs.append(_facility_at(graph, node, kind, f"{kind}:{i}"))
    return reqs, facs


@pytestmark_graph
def test_multiple_mixed_requirements_mostly_satisfied() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    reqs, facs = _build_mixed_requirements(graph, start_coord, dists, target_m, 3)

    routes = plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, reqs, facs)
    assert routes, "planner produced no candidates"

    best_satisfied_count = 0
    for route in routes:
        encounters = find_facility_encounters(route.candidate.geometry, facs)
        results = assign_requirements(reqs, encounters)
        best_satisfied_count = max(best_satisfied_count, sum(r.satisfied for r in results))

    # At least a majority satisfied by the real matcher -- this planner
    # proposes plausible candidates, it does not guarantee every
    # requirement lands exactly (see module docstring); `plan_routes`'s
    # scorer picks the best available.
    assert best_satisfied_count >= 2


@pytestmark_graph
def test_deterministic_repeated_calls() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    reqs, facs = _build_mixed_requirements(graph, start_coord, dists, target_m, 3)

    routes1 = plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, reqs, facs)
    routes2 = plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, reqs, facs)

    assert [r.node_path for r in routes1] == [r.node_path for r in routes2]


@pytestmark_graph
def test_shuffled_requirement_order_is_equivalent() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    reqs, facs = _build_mixed_requirements(graph, start_coord, dists, target_m, 4)

    routes_original = plan_constrained_out_and_back(
        graph, START, target_m, "out_and_back", 3, reqs, facs
    )

    shuffled = list(reqs)
    random.Random(42).shuffle(shuffled)
    routes_shuffled = plan_constrained_out_and_back(
        graph, START, target_m, "out_and_back", 3, shuffled, facs
    )

    # Same set of node paths (order of the requirement list must not
    # matter to which candidates are found), and each maps to the same
    # requirement-id -> satisfied outcome via the real Phase 1 matcher.
    paths_original = sorted(tuple(r.node_path) for r in routes_original)
    paths_shuffled = sorted(tuple(r.node_path) for r in routes_shuffled)
    assert paths_original == paths_shuffled

    for route in routes_original:
        encounters = find_facility_encounters(route.candidate.geometry, facs)
        results_orig = {
            r.requirement.id: r.satisfied for r in assign_requirements(reqs, encounters)
        }
        results_shuf = {
            r.requirement.id: r.satisfied for r in assign_requirements(shuffled, encounters)
        }
        assert results_orig == results_shuf


@pytestmark_graph
def test_final_distance_near_target() -> None:
    """Document the observed distance-error tolerance rather than assert
    an unrealistically tight bound the real street grid may not support."""
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    for n in (1, 3):
        reqs, facs = _build_mixed_requirements(graph, start_coord, dists, target_m, n)
        routes = plan_constrained_out_and_back(
            graph, START, target_m, "out_and_back", 3, reqs, facs
        )
        assert routes, f"planner produced no candidates for n={n}"
        best_error = min(abs(r.candidate.distance_m - target_m) for r in routes)
        # Observed on the real Manhattan graph: within ~35% of target for
        # small requirement counts (waypoint chaining and reuse-penalty
        # detours mean the outbound leg can overshoot target/2 before the
        # extension step even runs). Not a tight bound -- documents what
        # was actually observed, see module docstring for why this
        # planner does not do its own distance tuning (no length_tune).
        assert best_error <= target_m * 0.4, (
            f"n={n} best distance error {best_error} exceeds observed tolerance"
        )


@pytestmark_graph
def test_bounded_search_calls_scale_linearly_not_exponentially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real graph builds (`reuse_penalized_path`/`single_source_distances`
    calls) must stay bounded roughly linearly in requirement count, not
    blow up combinatorially, as requirement count grows from 2 to 6."""
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    call_counts: dict[int, int] = {}
    # Captured once, outside the loop, so a later iteration's wrapper
    # never wraps an earlier iteration's still-active wrapper (monkeypatch
    # only undoes at test teardown, not between loop iterations).
    orig_reuse = oab_planner.reuse_penalized_path  # type: ignore[attr-defined]
    orig_ssd = oab_planner.single_source_distances  # type: ignore[attr-defined]

    for n in (2, 6):
        reqs, facs = _build_mixed_requirements(graph, start_coord, dists, target_m, n)

        count = {"reuse": 0, "ssd": 0}

        def counted_reuse(*args: object, **kwargs: object) -> list[int] | None:
            count["reuse"] += 1
            return orig_reuse(*args, **kwargs)  # type: ignore[arg-type]

        def counted_ssd(*args: object, **kwargs: object) -> dict[int, float]:
            count["ssd"] += 1
            return orig_ssd(*args, **kwargs)  # type: ignore[arg-type]

        with monkeypatch.context() as ctx:
            ctx.setattr("app.facilities.oab_planner.reuse_penalized_path", counted_reuse)
            ctx.setattr("app.facilities.oab_planner.single_source_distances", counted_ssd)
            plan_constrained_out_and_back(graph, START, target_m, "out_and_back", 3, reqs, facs)

        call_counts[n] = count["reuse"] + count["ssd"]

    # Linear-in-requirements bound: MAX_FULL_BUILDS_PER_CALL full builds,
    # each doing at most (requirements) legs plus one extension-related
    # Dijkstra. Well under an exponential blowup (e.g. 6! orderings).
    max_builds = oab_planner.MAX_FULL_BUILDS_PER_CALL
    for n, total_calls in call_counts.items():
        assert total_calls <= max_builds * (n + 1), (
            f"n={n} made {total_calls} real-graph calls, exceeding the linear bound"
        )


# --- Requirement-adaptive build budget & cooperative deadline -----------


def test_adaptive_build_budget_by_requirement_count() -> None:
    assert _adaptive_build_budget(1) == oab_planner.MAX_FULL_BUILDS_PER_CALL
    assert _adaptive_build_budget(2) == 5
    assert _adaptive_build_budget(3) == 3
    assert _adaptive_build_budget(6) == 3


@pytestmark_graph
def test_already_expired_deadline_returns_no_candidates() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    radial = target_m / 6.0
    node = _node_near_bearing(graph, start_coord, dists, radial, 0.0, tol_b=180.0)
    assert node is not None, "graph topology near the test start point changed"

    req = FacilityRequirement(
        id="r1", kind="restroom",
        min_distance_m=dists[node] - 400, max_distance_m=dists[node] + 400,
    )
    fac = _facility_at(graph, node, "restroom", "restroom:1")

    routes = plan_constrained_out_and_back(
        graph, START, target_m, "out_and_back", 3, [req], [fac],
        deadline=PlanningDeadline(budget_s=-1.0),
    )
    assert routes == []


@pytestmark_graph
def test_deadline_expiring_mid_search_keeps_partial_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the deadline expires right after one useful build completes,
    that candidate must still be returned rather than discarded. Ties
    expiry to a successful build (not a raw call count) so the test
    doesn't depend on how many build attempts internally fail first."""
    build_count = {"n": 0}
    orig_build_plan = oab_planner._build_plan

    def counted_build_plan(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        result = orig_build_plan(*args, **kwargs)  # type: ignore[arg-type]
        if result is not None:
            build_count["n"] += 1
        return result

    monkeypatch.setattr("app.facilities.oab_planner._build_plan", counted_build_plan)

    class _ExpireAfterOneBuild(PlanningDeadline):
        def __init__(self) -> None:
            super().__init__(budget_s=9999.0)

        def expired(self) -> bool:
            return build_count["n"] >= 1

    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    radial = target_m / 6.0
    node = _node_near_bearing(graph, start_coord, dists, radial, 0.0, tol_b=180.0)
    assert node is not None, "graph topology near the test start point changed"

    req = FacilityRequirement(
        id="r1", kind="restroom",
        min_distance_m=dists[node] - 400, max_distance_m=dists[node] + 400,
    )
    fac = _facility_at(graph, node, "restroom", "restroom:1")

    routes = plan_constrained_out_and_back(
        graph, START, target_m, "out_and_back", 3, [req], [fac],
        deadline=_ExpireAfterOneBuild(),
    )
    assert len(routes) == 1


# --- Inner deadline checkpoints (`_build_plan` / `_extend_to_turnaround`) --


class _ExpireAfterNChecks(PlanningDeadline):
    """Expires starting from the (N+1)th call to `.expired()` -- lets a
    test allow exactly N checkpoints to pass before cutting off the
    next expensive operation, deterministically and without relying on
    real wall-clock timing."""

    def __init__(self, allow_calls: int) -> None:
        super().__init__(budget_s=9999.0)
        self._allow_calls = allow_calls
        self._calls = 0

    def expired(self) -> bool:
        self._calls += 1
        return self._calls > self._allow_calls


@pytestmark_graph
def test_build_plan_stops_between_waypoint_legs_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 waypoints along the same corridor: allow the top-level check and
    the first leg's check to pass, then expire before the second leg --
    the second `reuse_penalized_path` call must never happen, and the
    function must return None (no partial route)."""
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    n0 = _node_near_bearing(graph, start_coord, dists, target_m / 8.0, 0.0, tol_b=180.0)
    n1 = _node_near_bearing(graph, start_coord, dists, target_m / 5.0, 0.0, tol_b=180.0)
    n2 = _node_near_bearing(graph, start_coord, dists, target_m / 3.0, 0.0, tol_b=180.0)
    assert n0 is not None and n1 is not None and n2 is not None, (
        "graph topology near the test start point changed"
    )

    call_count = {"n": 0}
    orig_reuse = oab_planner.reuse_penalized_path  # type: ignore[attr-defined]

    def counted_reuse(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return orig_reuse(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(oab_planner, "reuse_penalized_path", counted_reuse)

    corridor_bearing = bearing_deg(start_coord, node_coordinate(graph, n2))
    # allow_calls=2: the top-of-function check (1) and the pre-leg check
    # for n1 (2) pass; the pre-leg check for n2 (3) does not.
    deadline = _ExpireAfterNChecks(allow_calls=2)

    result = oab_planner._build_plan(
        graph, start_node, [n0, n1, n2], paths, target_m, start_coord, corridor_bearing, deadline,
    )

    assert result is None
    assert call_count["n"] == 1  # only the n1 leg ran; n2's leg never started


@pytestmark_graph
def test_extend_to_turnaround_skips_distances_when_already_expired() -> None:
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    deadline = _ExpireAfterNChecks(allow_calls=0)

    result = oab_planner._extend_to_turnaround(
        graph, start_node, cumulative_so_far_m=1000.0, target_half_m=target_m / 2.0,
        start_coord=start_coord, corridor_bearing=0.0, used_pairs=set(), deadline=deadline,
    )

    assert result is None


@pytestmark_graph
def test_extend_to_turnaround_skips_extension_path_after_distances_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow the pre-`single_source_distances` check to pass, then expire
    before the extension's own `reuse_penalized_path` call -- distances
    get computed (needed to pick a turnaround target) but no extension
    leg is actually built."""
    graph = get_graph()
    target_m = 8000.0
    start_node = nearest_node(graph, START)
    dists, _paths = single_source_paths(graph, start_node)
    start_coord = node_coordinate(graph, start_node)

    reuse_calls = {"n": 0}
    orig_reuse = oab_planner.reuse_penalized_path  # type: ignore[attr-defined]

    def counted_reuse(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        reuse_calls["n"] += 1
        return orig_reuse(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(oab_planner, "reuse_penalized_path", counted_reuse)

    # allow_calls=1: the pre-single_source_distances check (1) passes;
    # the pre-extension-path check (2) does not.
    deadline = _ExpireAfterNChecks(allow_calls=1)

    result = oab_planner._extend_to_turnaround(
        graph, start_node, cumulative_so_far_m=1000.0, target_half_m=target_m / 2.0,
        start_coord=start_coord, corridor_bearing=0.0, used_pairs=set(), deadline=deadline,
    )

    assert result is None
    assert reuse_calls["n"] == 0
