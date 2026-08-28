"""Tests for the shared round-generator seam (`engine._round_pairs`):
ROUND_GENERATOR selects V1 vs polygon consistently for BOTH explicit
`shape="round"` and `shape="mix"`'s round component, closing the PR
#16/#17 gap where mix silently hardcoded V1 regardless of the flag.
V1 remains the default (see `engine._round_generator_version`);
`ROUND_GENERATOR=polygon` opts in.

Uses a dense grid graph (not the sparse star/spoke fixtures elsewhere in
this test suite) because polygon's multi-anchor loop needs real
alternate streets to route four legs without collapsing -- mirrors
`tests/generation/test_polygon_loop.py`'s `grid_graph` fixture.
"""

import networkx as nx
import pytest

import app.generation.engine as engine_module
from app.generation.engine import generate_candidates, generate_routes
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


def test_explicit_round_defaults_to_v1(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    monkeypatch.delenv("ROUND_GENERATOR", raising=False)
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

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


def test_mix_round_component_defaults_to_v1(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """Mirrors the explicit-round default: mix's round half must track
    the SAME default as explicit round, not silently diverge (the PR
    #16/#17 gap this migration closes)."""
    monkeypatch.delenv("ROUND_GENERATOR", raising=False)
    calls = {"polygon": 0, "v1": 0}
    monkeypatch.setattr(
        engine_module, "polygon_loop_pairs", _spy(calls, "polygon", real_polygon_loop_pairs)
    )
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "mix", 3)

    assert calls["v1"] >= 1
    assert calls["polygon"] == 0


def test_round_generator_polygon_env_makes_mix_round_component_use_polygon(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """The gap this migration closes: PR #16/#17 had mix hardcode V1
    for its round component regardless of ROUND_GENERATOR. This must no
    longer be true -- mix's round half goes through the same seam as
    explicit round."""
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
    """Only the round half of mix is affected by ROUND_GENERATOR -- the
    out_and_back half must keep calling the same generator regardless of
    the flag's value."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"oab": 0}
    monkeypatch.setattr(
        engine_module, "out_and_back_pairs", _spy(calls, "oab", real_out_and_back_pairs)
    )

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "mix", 3)

    assert calls["oab"] >= 1


def test_overcomplete_result_count_still_exposes_both_shapes(
    grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    """`facilities.orchestration.natural_match_pool`'s overcomplete-pool
    contract (result_count > count skips roundest-first truncation) must
    still hold regardless of which round generator is selected: both
    shapes should survive into the returned pool rather than round
    candidates (which structurally score higher on isoperimetric
    quotient) starving out every out_and_back candidate."""
    routes = generate_routes(
        grid_graph, start, TARGET_DISTANCE_M, "mix", count=8, result_count=8
    )

    shapes = {route.shape for route in routes}
    assert "round" in shapes
    assert "out_and_back" in shapes


def test_requested_final_count_is_respected(
    monkeypatch: pytest.MonkeyPatch, grid_graph: nx.MultiDiGraph, start: Coordinate
) -> None:
    for round_generator in ("v1", "polygon"):
        monkeypatch.setenv("ROUND_GENERATOR", round_generator)
        for shape in ("round", "out_and_back", "mix"):
            candidates = generate_candidates(grid_graph, start, TARGET_DISTANCE_M, shape, 3)
            assert len(candidates) <= 3


# ---------------------------------------------------------------------------
# Reliability fallback: polygon's own within-tolerance yield can be too
# thin in constrained local topology (see `engine._round_pairs`'s
# docstring and docs/benchmarks.md) -- these pin down the fallback
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
    all -- the fallback is a targeted top-up, not an unconditional
    blend of both generators on every request."""
    monkeypatch.setenv("ROUND_GENERATOR", "polygon")
    calls = {"v1": 0}
    monkeypatch.setattr(engine_module, "round_pairs", _spy(calls, "v1", real_round_pairs))

    generate_candidates(grid_graph, start, TARGET_DISTANCE_M, "round", 3)

    assert calls["v1"] == 0
