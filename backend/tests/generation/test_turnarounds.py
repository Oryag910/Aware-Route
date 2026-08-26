import networkx as nx

from app.generation.turnarounds import select_turnarounds
from app.graph.distances import single_source_distances
from app.routing.geometry import bearing_deg, haversine_m
from app.routing.provider import Coordinate
from tests.generation.conftest import ORIGIN, ORIGIN_NODE, dense_sector_dists


def _dists(star_graph: nx.MultiDiGraph) -> dict[int, float]:
    return single_source_distances(star_graph, ORIGIN_NODE)


def test_selects_nodes_within_radius_window(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    # Target 1000m: only the outer ring (1000m) is within +/-25%; the
    # inner ring (500m) is well outside and must be excluded.
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    picked_dists = [dist for _, dist in picked]

    assert picked_dists
    assert all(750.0 <= dist <= 1250.0 for dist in picked_dists)


def test_excludes_inner_ring_at_outer_target(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    inner_ids = {101, 111, 121, 131}
    assert not (set(node for node, _ in picked) & inner_ids)


def test_bearing_diversity_one_per_sector(
    star_graph: nx.MultiDiGraph,
) -> None:
    dists = _dists(star_graph)
    # The four outer spokes point N/E/S/W -- four distinct 45deg sectors,
    # so all four should be returned (never two from the same direction).
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    assert len(picked) == 4

    bearings = [
        round(
            bearing_deg(ORIGIN, _coord(star_graph, node)) / 45.0
        )
        for node, _ in picked
    ]
    assert len(set(b % 8 for b in bearings)) == 4


def test_count_caps_result(star_graph: nx.MultiDiGraph) -> None:
    dists = _dists(star_graph)
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=2
    )
    assert len(picked) == 2


def _coord(graph: nx.MultiDiGraph, node: int) -> Coordinate:
    data = graph.nodes[node]
    return Coordinate(lat=data["y"], lon=data["x"])


def test_multi_sector_diversity_preserved_when_asking_for_more(
    star_graph: nx.MultiDiGraph,
) -> None:
    """When sectors are already diverse (the common case), the fallback
    path must not change anything -- one winner per populated sector,
    same as before this fallback existed."""
    dists = _dists(star_graph)
    picked = select_turnarounds(
        star_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=8
    )
    assert len(picked) == 4
    bearings = [
        round(bearing_deg(ORIGIN, _coord(star_graph, node)) / 45.0) % 8
        for node, _ in picked
    ]
    assert len(set(bearings)) == 4


def test_single_sector_fallback_returns_separated_turnarounds(
    dense_sector_graph: nx.MultiDiGraph,
) -> None:
    """All 4 eligible nodes sit in ONE bearing sector -- the exact
    starvation pattern that used to cap `select_turnarounds` at a single
    result regardless of `count`. Asking for 3 should now return 3,
    pulled from the same sector's fallback candidates."""
    dists = dense_sector_dists()
    picked = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=3
    )
    assert len(picked) == 3


def test_single_sector_fallback_skips_adjacent_junk(
    dense_sector_graph: nx.MultiDiGraph,
) -> None:
    """Node 202 sits ~0.9m from the pass-1 winner (node 201) -- an
    adjacent node on the same stretch of street that would produce a
    near-identical route. It must never be picked over the genuinely
    separated nodes 203/204."""
    dists = dense_sector_dists()
    picked = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=3
    )
    picked_ids = {node for node, _ in picked}
    assert picked_ids == {201, 203, 204}


def test_single_sector_fallback_picks_are_mutually_separated(
    dense_sector_graph: nx.MultiDiGraph,
) -> None:
    dists = dense_sector_dists()
    picked = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=3
    )
    coords = [_coord(dense_sector_graph, node) for node, _ in picked]
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            assert haversine_m(coords[i], coords[j]) >= 250.0


def test_count_remains_upper_bound_with_fallback(
    dense_sector_graph: nx.MultiDiGraph,
) -> None:
    """Only 3 of the 4 dense-sector nodes are separated enough to
    qualify (202 is junk) -- asking for a larger count than that must
    not error, and must still respect `count` as an upper bound."""
    dists = dense_sector_dists()
    picked = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=10
    )
    assert len(picked) <= 10
    assert len(picked) == 3  # only 3 separation-qualifying candidates exist


def test_selection_is_deterministic(dense_sector_graph: nx.MultiDiGraph) -> None:
    dists = dense_sector_dists()
    first = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=3
    )
    second = select_turnarounds(
        dense_sector_graph, ORIGIN_NODE, dists, target_radius_m=1000.0, count=3
    )
    assert first == second
