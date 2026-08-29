"""Tests for the shared round-generator seam (`engine._round_pairs`):
ROUND_GENERATOR selects V1 vs polygon consistently for both explicit
`shape="round"` and `shape="mix"`'s round component, closing the PR
#16/#17 gap where mix used to hardcode V1 regardless of the flag.
`"auto"` is the default (see `engine._round_generator_version`):
polygon for `requested_count<=3`, v1 for `requested_count>=4`.
`ROUND_GENERATOR=v1`/`=polygon` force a single generator regardless of
requested_count.

Uses a dense grid graph (not the sparse star/spoke fixtures elsewhere in
this test suite) because polygon's multi-anchor loop needs real
alternate streets to route four legs without collapsing. Mirrors
`tests/generation/test_polygon_loop.py`'s `grid_graph` fixture.
"""

from typing import Any

import networkx as nx
import pytest

import app.generation.engine as engine_module
from app.facilities.models import FacilityRequirement
from app.facilities.orchestration import (
    NATURAL_POOL_CEILING,
    NATURAL_POOL_MULTIPLIER,
    NO_FACILITY_POOL_CEILING,
    NO_FACILITY_POOL_MULTIPLIER,
    natural_match_pool,
)
from app.generation.engine import (
    Shape,
    _round_generator_version,
    generate_candidates,
    generate_routes,
)
from app.generation.out_and_back import out_and_back_pairs as real_out_and_back_pairs
from app.generation.polygon_loop import polygon_loop_pairs as real_polygon_loop_pairs
from app.generation.round_route import round_pairs as real_round_pairs
from app.routing.geometry import destination_point, haversine_m
from app.routing.provider import Coordinate


GRID_N = 21
GRID_SPACING_M = 50.0
GRID_ORIGIN = Coordinate(lat=40.750, lon=-73.980)
GRID_CENTER_INDEX = GRID_N // 2

TARGET_DISTANCE_M = 1200.0


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


@pytest.fixture()
def start() -> Coordinate:
    return _node_coord(GRID_CENTER_INDEX, GRID_CENTER_INDEX)


def _spy(counter: dict[str, int], key: str, real: object) -> object:
    def wrapped(*args: object, **kwargs: object) -> object:
        counter[key] += 1
        return real(*args, **kwargs)  # type: ignore[operator]

    return wrapped


def test_explicit_round_no_env_defaults_to_auto_selects_polygon_at_count_3(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """No `ROUND_GENERATOR` set at all must resolve to `"auto"`, which
    picks polygon at the product's own count=3 default
    (`generate_candidates`'s `count` here doubles as `requested_count`
    since it's omitted)."""
    monkeypatch.delenv("ROUND_GENERATOR", raising=False)
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["polygon"] >= 1
    assert calls["v1"] == 0


def test_explicit_round_no_env_defaults_to_auto_selects_v1_at_count_5(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """Same no-env default, but at the API's max count=5, where auto
    must still fall back to v1, exactly like an explicit
    `ROUND_GENERATOR=auto` would."""
    monkeypatch.delenv("ROUND_GENERATOR", raising=False)
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 5)

    assert calls["v1"] >= 1
    assert calls["polygon"] == 0


def test_round_generator_polygon_env_opts_in_for_explicit_round(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["polygon"] >= 1


def test_mix_round_component_no_env_defaults_to_auto_selects_polygon_at_count_3(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """Mirrors the explicit-round default: mix's round half must track
    the same default as explicit round (the PR #16/#17 gap this
    migration closes)."""
    monkeypatch.delenv("ROUND_GENERATOR", raising=False)
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "mix", 3)

    assert calls["polygon"] >= 1
    assert calls["v1"] == 0


def test_round_generator_polygon_env_makes_mix_round_component_use_polygon(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """The gap this migration closes: PR #16/#17 had mix hardcode V1
    for its round component regardless of ROUND_GENERATOR. Mix's round
    half now goes through the same seam as explicit round."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "mix", 3)

    assert calls["polygon"] >= 1


def test_mix_still_uses_ordinary_out_and_back_generator(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """Only the round half of mix is affected by ROUND_GENERATOR; the
    out_and_back half must keep calling the same generator regardless of
    the flag's value."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"oab": 0}
    monkeypatch.setattr(
        engine_module, "out_and_back_pairs", _spy(calls, "oab", real_out_and_back_pairs)
    )

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "mix", 3)

    assert calls["oab"] >= 1


@pytest.mark.parametrize("round_generator", ["v1", "polygon"])
def test_overcomplete_result_count_still_exposes_both_shapes(
    monkeypatch: pytest.MonkeyPatch,
    grid_graph: nx.MultiDiGraph,
    start: Coordinate,
    round_generator: str,
) -> None:
    """`facilities.orchestration.natural_match_pool`'s overcomplete-pool
    contract (result_count > count skips roundest-first truncation) must
    still hold regardless of which round generator is selected: both
    shapes should survive into the returned pool rather than round
    candidates (which structurally score higher on isoperimetric
    quotient) starving out every out_and_back candidate. This is an
    architectural guarantee of `generate_routes`'s mix-pool handling,
    not something either round generator should be able to break, so
    it's exercised under both."""
    monkeypatch.setenv("ROUND_GENERATOR", round_generator)

    routes = generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "mix", count=8, result_count=8
    )

    shapes = {route.shape for route in routes}
    assert "round" in shapes
    assert "out_and_back" in shapes


@pytest.mark.parametrize("round_generator", ["v1", "polygon"])
@pytest.mark.parametrize("shape", ["round", "out_and_back", "mix"])
def test_requested_final_count_is_respected(
    monkeypatch: pytest.MonkeyPatch,
    grid_graph: nx.MultiDiGraph,
    start: Coordinate,
    round_generator: str,
    shape: Shape,
) -> None:
    """The real product contract is an exact count, not merely "at most":
    `test_no_facilities_returns_full_requested_count` in
    tests/test_generic_routes.py asserts this end-to-end on the real
    Manhattan graph. This dense, well-connected synthetic grid has
    plenty of turnaround/template alternatives for every generator/shape
    combination at this target distance (confirmed deterministic across
    repeated runs), so it can assert the same exact-count contract
    directly against the engine layer, for both round generators."""
    monkeypatch.setenv("ROUND_GENERATOR", round_generator)

    candidates = generate_candidates(grid_graph, start, TARGET_DISTANCE_M, shape, 3)

    assert len(candidates) == 3


# ---------------------------------------------------------------------------
# Reliability fallback: polygon's own within-tolerance yield can be too
# thin in constrained local topology (see `engine._round_pairs`'s
# docstring and docs/benchmarks.md). These tests pin down the fallback
# mechanism directly rather than relying on real-graph geography to
# reproduce the shortfall.
# ---------------------------------------------------------------------------


def test_polygon_low_tolerance_yield_triggers_v1_fallback(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """When polygon's own pool doesn't clear `MIN_WITHIN_TOLERANCE_FLOOR`
    in-tolerance candidates, V1 is asked to top up the pool."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")

    def starved_polygon(*args: object, **kwargs: object) -> list[object]:
        return []  # simulates polygon finding nothing in-tolerance

    monkeypatch.setattr(engine_module, "polygon_loop_pairs", starved_polygon)
    calls = {"v1": 0}
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["v1"] >= 1


def test_polygon_healthy_tolerance_yield_skips_v1_fallback(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """When polygon alone already clears the floor, V1 must not run at
    all: the fallback is a targeted top-up, not an unconditional blend
    of both generators on every request."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"v1": 0}
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["v1"] == 0


# ---------------------------------------------------------------------------
# ROUND_GENERATOR="auto": selects the generator off the user's real
# requested final count, never the (often much larger) internal
# candidate-pool size. See `engine._round_generator_version`'s
# docstring. `"auto"` is the no-env default (see the dedicated no-env
# tests above); the tests below set the env var explicitly to exercise
# "auto"'s selection logic directly, independent of default-value
# bookkeeping.
# ---------------------------------------------------------------------------


# (A) No-env / auto selection is driven by requested_count, never pool size.


@pytest.mark.parametrize("requested_count", [1, 2, 3])
def test_auto_selects_polygon_for_requested_count_at_or_below_threshold(
    requested_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    assert _round_generator_version(requested_count) == "polygon"


@pytest.mark.parametrize("requested_count", [4, 5])
def test_auto_selects_v1_for_requested_count_above_threshold(
    requested_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    assert _round_generator_version(requested_count) == "v1"


@pytest.mark.parametrize("pool_size", [3, 9, 12])
def test_auto_selects_polygon_for_requested_count_3_regardless_of_pool_size(
    pool_size: int, monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """The important cases here are pool_size=9 and pool_size=12: the
    exact overcomplete pool sizes `facilities.orchestration.natural_match_pool`
    requests for a real `count=3` ask (see (D) below). `_round_pairs`
    receives `pool_size` as its `count` argument (candidate construction
    size, unchanged role) but `requested_count=3` separately, and
    selection must track the latter, not the former."""
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "round", pool_size,
        result_count=pool_size, requested_count=3,
    )

    assert calls["polygon"] >= 1


@pytest.mark.parametrize("requested_count", [4, 5])
def test_auto_selects_v1_end_to_end_for_requested_count_above_threshold(
    requested_count: int,
    monkeypatch: pytest.MonkeyPatch,
    grid_graph: nx.MultiDiGraph,
    start: Coordinate,
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "round", requested_count,
        result_count=requested_count, requested_count=requested_count,
    )

    assert calls["v1"] >= 1
    assert calls["polygon"] == 0


# (B) Explicit overrides ignore requested_count entirely.


@pytest.mark.parametrize("requested_count", [1, 2, 3, 4, 5])
def test_explicit_v1_override_ignores_requested_count(
    requested_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "v1")
    assert _round_generator_version(requested_count) == "v1"


@pytest.mark.parametrize("requested_count", [1, 2, 3, 4, 5])
def test_explicit_polygon_override_ignores_requested_count(
    requested_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    assert _round_generator_version(requested_count) == "polygon"


def test_invalid_round_generator_value_fails_safe_to_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "not-a-real-mode")
    assert _round_generator_version(3) == "v1"
    assert _round_generator_version(5) == "v1"


# (C) Mix's round component follows the same auto logic as explicit round.


def test_auto_mix_round_component_uses_polygon_for_requested_count_3(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "mix", 8, result_count=8, requested_count=3
    )

    assert calls["polygon"] >= 1


def test_auto_mix_round_component_uses_v1_for_requested_count_5(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "mix", 8, result_count=8, requested_count=5
    )

    assert calls["v1"] >= 1
    assert calls["polygon"] == 0


def test_auto_mix_out_and_back_component_unaffected(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """`ROUND_GENERATOR=auto` (and requested_count) must only steer the
    round half of mix; out_and_back generation is untouched regardless."""
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"oab": 0}
    monkeypatch.setattr(
        engine_module, "out_and_back_pairs", _spy(calls, "oab", real_out_and_back_pairs)
    )

    generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "mix", 8, result_count=8, requested_count=5
    )

    assert calls["oab"] >= 1


# (D) `natural_match_pool` passes the inflated pool size as `count`/
# `result_count` (construction size, unchanged behavior) but the REAL
# user count separately as `requested_count` (generator selection only).


def test_natural_match_pool_no_facility_separates_pool_size_from_requested_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_generate_routes(*args: object, **kwargs: object) -> list[object]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(
        "app.facilities.orchestration.generate_routes", fake_generate_routes
    )

    natural_match_pool(
        graph=object(),
        start=Coordinate(lat=40.75, lon=-73.98),
        target_distance_m=1200.0,
        shape="round",
        count=3,
        requirements=[],
    )

    expected_pool = min(
        NO_FACILITY_POOL_CEILING, max(3 * NO_FACILITY_POOL_MULTIPLIER, 3)
    )
    assert expected_pool == 9
    assert captured["args"][4] == expected_pool  # count/pool-size positional arg
    assert captured["kwargs"]["result_count"] == expected_pool
    assert captured["kwargs"]["requested_count"] == 3


def test_natural_match_pool_with_facility_separates_pool_size_from_requested_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_generate_routes(*args: object, **kwargs: object) -> list[object]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(
        "app.facilities.orchestration.generate_routes", fake_generate_routes
    )

    requirement = FacilityRequirement(
        id="req-1", kind="restroom", min_distance_m=1000.0, max_distance_m=3000.0
    )
    natural_match_pool(
        graph=object(),
        start=Coordinate(lat=40.75, lon=-73.98),
        target_distance_m=1200.0,
        shape="round",
        count=3,
        requirements=[requirement],
    )

    expected_pool = min(NATURAL_POOL_CEILING, max(3 * NATURAL_POOL_MULTIPLIER, 3))
    assert expected_pool == 12
    assert captured["args"][4] == expected_pool
    assert captured["kwargs"]["result_count"] == expected_pool
    assert captured["kwargs"]["requested_count"] == 3


# (E) Direct/internal callers that omit `requested_count` keep existing
# behavior: it defaults to `count`, so pre-existing call sites
# (generate_candidates, benchmark scripts, other tests) are unaffected.


def test_requested_count_defaults_to_count_when_omitted(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    # No requested_count passed: generate_candidates never threads one
    # through, so it must fall back to count=5, which auto maps to v1.
    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 5)

    assert calls["v1"] >= 1
    assert calls["polygon"] == 0


def test_requested_count_defaults_to_count_3_selects_polygon(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.setenv("ROUND_GENERATOR", "auto")
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["polygon"] >= 1


# (F) `RouteRequest.count` contract (default 3, max 5) is unmodified by
# this migration. This is a smoke check; the canonical contract test
# lives alongside the model in `tests/test_generic_routes.py` /
# `app/main.py`.


def test_route_request_count_default_and_bounds_unchanged() -> None:
    from app.main import RouteRequest

    field = RouteRequest.model_fields["count"]
    assert field.default == 3
    assert field.metadata[0].ge == 1
    assert field.metadata[1].le == 5
