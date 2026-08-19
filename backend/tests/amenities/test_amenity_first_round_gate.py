"""Tests for the amenity-first round correctness gate
(MAX_AMENITY_ROUND_EDGE_REUSE_RATIO / _tuned_round_or_reject).

An explicit shape="round" request must not silently return a
materially out-and-back amenity-first route. See
app/generation/amenity_first.py's module docstring for the
537-scenario threshold study that grounds the 0.15 constant.

The real-graph tests are skipped when the committed Manhattan graph
artifact isn't built, matching the existing pattern in
tests/test_local_engine_endpoint.py. They pin down the exact production
reproduction from the investigation (a restroom close to the start
relative to the target forces a large tuner spur; a restroom near half
the target radius does not).
"""

import networkx as nx
import pytest

from app.amenities.models import Amenity
from app.amenities.snapping import snap_amenities
from app.generation.amenity_first import (
    MAX_AMENITY_ROUND_EDGE_REUSE_RATIO,
    _out_and_back_placed,
    _round_through_pair,
    _tuned_round_or_reject,
    through_amenities_pairs,
)
from app.generation.out_and_back import out_and_back_pairs
from app.generation.quality import edge_reuse_ratio
from app.generation.round_route import round_pairs
from app.graph.distances import nearest_node, single_source_distances, single_source_paths
from app.graph.loader import GRAPH_PATH, get_graph
from app.graph.model import node_coordinate
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint
from tests.amenities.conftest import ORIGIN_NODE


MI = 1609.34

needs_graph = pytest.mark.skipif(
    not GRAPH_PATH.exists(),
    reason="graph artifact not built (run scripts/build_graph.py)",
)

# Same start/target/range as the reported production bug (see the
# investigation: a real "round" + "matched" route returned with
# repeated_segment_ratio == 0.5).
_START = Coordinate(lat=40.7674, lon=-73.9818)
_TARGET_M = 5 * MI
_MIN_RANGE_M = 1 * MI
_MAX_RANGE_M = 4 * MI


def _node_near_distance(
    dists: dict[int, float], target_d: float, min_m: float, max_m: float, tol: float = 150.0
) -> int:
    best: int | None = None
    best_err = float("inf")
    for node, d in dists.items():
        if min_m <= d <= max_m:
            err = abs(d - target_d)
            if err < best_err:
                best_err, best = err, node
    assert best is not None and best_err <= tol, (
        "no graph node found near the expected test distance -- graph "
        "topology near the test start point may have changed"
    )
    return best


# ---------------------------------------------------------------------
# 1 & 3: real-graph accept/reject, pinned to the investigation's exact
# production reproduction.
# ---------------------------------------------------------------------


@needs_graph
def test_round_through_pair_rejects_spur_dominant_candidate() -> None:
    """A restroom ~1mi from start against a 5mi target forces the core
    amenity loop (~2x1mi) to undershoot the target by ~4.8km; the tuner
    pads that with a large prepended out-and-back spur. This is the
    exact mechanism behind the reported production bug and must now be
    rejected rather than returned mislabeled 'round'."""
    graph = get_graph()
    start_node = nearest_node(graph, _START)
    dists, paths = single_source_paths(graph, start_node)
    close_node = _node_near_distance(dists, 1.0 * MI, _MIN_RANGE_M, _MAX_RANGE_M)

    result = _round_through_pair(graph, start_node, dists, close_node, _TARGET_M, paths)

    assert result is None


@needs_graph
def test_round_through_pair_accepts_clean_candidate() -> None:
    """A restroom near half the target radius produces a genuinely
    divergent loop needing little/no spur padding -- must be accepted,
    and the accepted route must itself respect the ceiling."""
    graph = get_graph()
    start_node = nearest_node(graph, _START)
    dists, paths = single_source_paths(graph, start_node)
    far_node = _node_near_distance(dists, 2.5 * MI, _MIN_RANGE_M, _MAX_RANGE_M)

    result = _round_through_pair(graph, start_node, dists, far_node, _TARGET_M, paths)

    assert result is not None
    candidate, node_path = result
    assert edge_reuse_ratio(node_path) <= MAX_AMENITY_ROUND_EDGE_REUSE_RATIO
    assert abs(candidate.distance_m - _TARGET_M) <= 250.0


@needs_graph
def test_through_amenities_pairs_continues_past_a_rejected_placement() -> None:
    """When the closest-to-midpoint amenity produces a rejected
    (spur-dominant) round candidate, through_amenities_pairs must still
    surface a round candidate from a different, cleaner placement --
    not abort the whole round pool for this shape."""
    graph = get_graph()
    start_node = nearest_node(graph, _START)
    dists, paths = single_source_paths(graph, start_node)

    close_node = _node_near_distance(dists, 1.0 * MI, _MIN_RANGE_M, _MAX_RANGE_M)
    far_node = _node_near_distance(dists, 2.5 * MI, _MIN_RANGE_M, _MAX_RANGE_M)
    close_coord = node_coordinate(graph, close_node)
    far_coord = node_coordinate(graph, far_node)

    amenities = [
        Amenity(lat=close_coord.lat, lon=close_coord.lon, kind="restroom", name="close"),
        Amenity(lat=far_coord.lat, lon=far_coord.lon, kind="restroom", name="far"),
    ]
    snapped = snap_amenities(graph, amenities, max_snap_m=200.0)

    triples = through_amenities_pairs(
        graph, _START, _TARGET_M, snapped, _MIN_RANGE_M, _MAX_RANGE_M,
        "round", 3, dists, paths,
    )

    round_triples = [t for t in triples if t[2] == "round"]
    assert round_triples, "expected the clean placement to still produce a round candidate"
    for candidate, node_path, _shape in round_triples:
        assert edge_reuse_ratio(node_path) <= MAX_AMENITY_ROUND_EDGE_REUSE_RATIO


# ---------------------------------------------------------------------
# 4: boundary `>` semantics, isolated from graph/generation entirely by
# monkeypatching tune_pair_to_target to a fixed, hand-computed result.
# ---------------------------------------------------------------------


def _partial_retrace_path(out_hops: int, reused_hops: int) -> list[int]:
    """A node path with an EXACT, hand-computable edge_reuse_ratio: walk
    `out_hops` new hops, then walk back over the last `reused_hops` of
    them. edge_reuse_ratio == reused_hops / (out_hops + reused_hops)."""
    outbound = list(range(out_hops + 1))
    back = list(range(out_hops - 1, out_hops - 1 - reused_hops, -1))
    return outbound + back


def _dummy_candidate(node_path: list[int]) -> RouteCandidate:
    geometry = tuple(
        RoutePoint(lat=0.0, lon=0.0001 * i, elevation_m=0.0) for i in node_path
    )
    return RouteCandidate(geometry=geometry, distance_m=1000.0, elevation_gain_m=0.0)


def test_tuned_round_or_reject_boundary_is_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ratio exactly at the ceiling (0.15) is accepted (`>`, not `>=`);
    just above it is rejected."""
    at_boundary = _partial_retrace_path(out_hops=85, reused_hops=15)  # 15/100 == 0.15
    just_over = _partial_retrace_path(out_hops=84, reused_hops=16)  # 16/100 == 0.16
    assert edge_reuse_ratio(at_boundary) == pytest.approx(0.15)
    assert edge_reuse_ratio(just_over) == pytest.approx(0.16)

    def fake_tune(
        graph: object,
        start_node: int,
        candidate: RouteCandidate,
        node_path: list[int],
        target_distance_m: float,
        **kwargs: object,
    ) -> tuple[RouteCandidate, list[int]]:
        return candidate, node_path

    monkeypatch.setattr("app.generation.amenity_first.tune_pair_to_target", fake_tune)

    accepted = _tuned_round_or_reject(
        None, 0, _dummy_candidate(at_boundary), at_boundary, 1000.0, {}, None,
    )
    rejected = _tuned_round_or_reject(
        None, 0, _dummy_candidate(just_over), just_over, 1000.0, {}, None,
    )

    assert accepted is not None
    assert rejected is None


# ---------------------------------------------------------------------
# 2: guaranteed rejection on a graph with no divergent return path at
# all (a pure tree/star has exactly one path between any two nodes, so
# every round-loop attempt is a literal there-and-back).
# ---------------------------------------------------------------------


def test_round_through_pair_rejects_on_a_tree_graph(spoke_graph: nx.MultiDiGraph) -> None:
    """spoke_graph is a star with no cycles -- there is no alternate
    street to return by, so the reuse-penalized return leg is always
    the outbound path reversed (edge_reuse_ratio == 0.5) regardless of
    penalty tier. This must be rejected, not returned as 'round'."""
    dists = single_source_distances(spoke_graph, ORIGIN_NODE)
    # 102 is the outer node of the first spoke (bearing 0), 1000m out.
    amenity_node = 102

    result = _round_through_pair(spoke_graph, ORIGIN_NODE, dists, amenity_node, 2000.0)

    assert result is None


# ---------------------------------------------------------------------
# 6 & 7: ordinary round and out_and_back generation are unaffected --
# they never call the gated helper at all, so even a candidate that
# WOULD exceed the ceiling is still returned.
# ---------------------------------------------------------------------


def test_ordinary_round_pairs_unaffected_by_the_gate(spoke_graph: nx.MultiDiGraph) -> None:
    """round_pairs on the same tree graph -- which produces nothing but
    edge_reuse_ratio == 0.5 loops -- still returns candidates, because
    the gate lives only in _round_through_pair (amenity-first), never
    in the ordinary generator."""
    dists = single_source_distances(spoke_graph, ORIGIN_NODE)

    pairs = round_pairs(spoke_graph, ORIGIN_NODE, dists, 2000.0, count=3)

    assert pairs
    for candidate, node_path in pairs:
        assert edge_reuse_ratio(node_path) > MAX_AMENITY_ROUND_EDGE_REUSE_RATIO


def test_out_and_back_amenity_placement_unaffected_by_the_gate(
    spoke_graph: nx.MultiDiGraph,
) -> None:
    """_out_and_back_placed (the amenity-first out_and_back builder) is
    definitionally high-retrace (it walks the same street back) and must
    never be gated -- the gate is round-only."""
    dists = single_source_distances(spoke_graph, ORIGIN_NODE)
    amenity_node = 101  # inner node of the first spoke, 500m out

    result = _out_and_back_placed(
        spoke_graph, ORIGIN_NODE, dists, amenity_node, 500.0, 2000.0, on_return=False,
    )

    assert result is not None
    _candidate, node_path = result
    assert edge_reuse_ratio(node_path) > MAX_AMENITY_ROUND_EDGE_REUSE_RATIO


def test_out_and_back_pairs_unaffected_by_the_gate(spoke_graph: nx.MultiDiGraph) -> None:
    """Ordinary (non-amenity) out_and_back_pairs is likewise never
    gated."""
    dists = single_source_distances(spoke_graph, ORIGIN_NODE)

    pairs = out_and_back_pairs(spoke_graph, ORIGIN_NODE, dists, 2000.0, count=3)

    assert pairs
    for candidate, node_path in pairs:
        assert edge_reuse_ratio(node_path) > MAX_AMENITY_ROUND_EDGE_REUSE_RATIO
