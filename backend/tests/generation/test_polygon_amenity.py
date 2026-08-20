import networkx as nx
import pytest

from app.amenities.matching import match_amenities_to_route
from app.amenities.models import Amenity
from app.amenities.snapping import SnappedAmenity
from app.generation.polygon_amenity import (
    AMENITY_RADIAL_REACH_MARGIN,
    _try_amenity_placement,
    polygon_loop_through_amenities_pairs,
)
from app.generation.polygon_loop import MAX_EDGE_REUSE_RATIO, _NodeIndex
from app.generation.polygon_template import TEMPLATES
from app.generation.quality import edge_reuse_ratio
from app.generation.shape_metrics import isoperimetric_quotient
from app.graph.distances import single_source_paths
from app.graph.model import node_coordinate
from app.routing.geometry import destination_point, haversine_m
from app.routing.provider import Coordinate
from scripts.benchmark_suite import _short_start_return_spur


# Same dense grid fixture as test_polygon_loop.py (41x41, 50m spacing,
# centered start) -- large enough to contain every TEMPLATES rectangle
# at TARGET_DISTANCE_M with margin, dense enough for reuse-penalized
# legs to have real alternate streets.
GRID_N = 41
GRID_SPACING_M = 50.0
GRID_ORIGIN = Coordinate(lat=40.750, lon=-73.980)
GRID_CENTER_INDEX = GRID_N // 2

TARGET_DISTANCE_M = 2400.0
COUNT = 5


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
def start_node() -> int:
    return _node_id(GRID_CENTER_INDEX, GRID_CENTER_INDEX)


def _amenity_at(i: int, j: int, kind: str = "restroom") -> SnappedAmenity:
    coord = _node_coord(i, j)
    return SnappedAmenity(
        amenity=Amenity(lat=coord.lat, lon=coord.lon, kind=kind, name=f"test-{kind}"),  # type: ignore[arg-type]
        node_id=_node_id(i, j),
        snap_distance_m=0.0,
    )


# A node reachable within AMENITY_RADIAL_REACH_MARGIN * TARGET_DISTANCE_M
# of start (~1008m) at roughly the requested mid-range cumulative
# fraction -- east and slightly north of center, a plausible "on one
# leg of a broad loop" position without forcing a there-and-back detour.
RESTROOM_OFFSET = (GRID_CENTER_INDEX + 8, GRID_CENTER_INDEX + 6)
FOUNTAIN_OFFSET = (GRID_CENTER_INDEX + 7, GRID_CENTER_INDEX - 6)

MIN_RANGE_M = 0.25 * TARGET_DISTANCE_M
MAX_RANGE_M = 0.75 * TARGET_DISTANCE_M


# ---------------------------------------------------------------------------
# 1-8: core construction, cumulative range, non-turnaround, quality
# ---------------------------------------------------------------------------


def test_restroom_on_target_leg_is_on_returned_route(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert triples
    assert any(restroom.node_id in node_path for _c, node_path, _s in triples)


def test_restroom_actual_cumulative_position_in_range(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    candidate, _node_path, _shape = triples[0]
    matches = match_amenities_to_route(candidate.geometry, [restroom.amenity])
    assert matches
    assert MIN_RANGE_M <= matches[0].mile_marker_m <= MAX_RANGE_M


def test_restroom_is_not_the_turnaround(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # The route must keep going meaningfully after the restroom, not
    # fold back immediately the way a there-and-back turnaround would.
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    _candidate, node_path, _shape = triples[0]
    restroom_index = node_path.index(restroom.node_id)
    hops_after = len(node_path) - 1 - restroom_index
    total_hops = len(node_path) - 1

    # A there-and-back fold would put the restroom at ~50% of the
    # path's hops with a mirrored (reversed) tail; a genuine multi-leg
    # loop continues on to at least 2 more distinct corners after it.
    assert hops_after >= 0.15 * total_hops
    assert edge_reuse_ratio(node_path) <= MAX_EDGE_REUSE_RATIO


def test_amenity_loop_remains_reasonably_compact(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    best_candidate, _node_path, _shape = triples[0]
    assert isoperimetric_quotient(best_candidate.geometry) > 0.25


def test_low_edge_reuse_on_amenity_candidates(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    for _candidate, node_path, _shape in triples:
        assert edge_reuse_ratio(node_path) <= MAX_EDGE_REUSE_RATIO


def test_no_short_start_return_spur(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    for candidate, _node_path, _shape in triples:
        assert _short_start_return_spur(candidate.geometry) is False


def test_distance_tuning_stays_close_to_target(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples

    best_error = min(abs(c.distance_m - TARGET_DISTANCE_M) for c, _p, _s in triples)
    # Looser than production tolerance -- coarse 50m-edge grid, same
    # rationale as test_polygon_loop.py's own distance-tuning test.
    assert best_error <= 400.0


def test_rescaling_preserves_the_same_amenity(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # The tuned/rescaled final candidate must still route through the
    # SAME amenity node it was built for, not a different one swapped
    # in mid-tuning.
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    start_coord = node_coordinate(grid_graph, start_node)
    node_index = _NodeIndex(grid_graph)
    _dists, paths = single_source_paths(grid_graph, start_node)

    from app.generation.polygon_amenity import _best_templates_for_amenity

    target_fraction = (MIN_RANGE_M + MAX_RANGE_M) / 2.0 / TARGET_DISTANCE_M
    amenity_coord = Coordinate(lat=restroom.amenity.lat, lon=restroom.amenity.lon)
    template, leg = _best_templates_for_amenity(
        start_coord, TARGET_DISTANCE_M, target_fraction, amenity_coord
    )[0]

    result = _try_amenity_placement(
        grid_graph, start_node, node_index, start_coord, TARGET_DISTANCE_M,
        restroom, template, leg, MIN_RANGE_M, MAX_RANGE_M, 100.0, paths,
    )

    assert result is not None
    assert restroom.node_id in result.node_path


# ---------------------------------------------------------------------------
# 9-13: selection, priority, fallback, rejection, empty pool
# ---------------------------------------------------------------------------


def test_multiple_amenities_picks_a_valid_one_near_window(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom_a = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    restroom_b = _amenity_at(GRID_CENTER_INDEX - 8, GRID_CENTER_INDEX + 6, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom_a, restroom_b], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert triples
    used_nodes = {restroom_a.node_id, restroom_b.node_id}
    assert any(
        any(node in used_nodes for node in node_path) for _c, node_path, _s in triples
    )


def test_restroom_preferred_over_fountain_when_both_valid(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    fountain = _amenity_at(*FOUNTAIN_OFFSET, kind="fountain")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [fountain, restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert triples
    top_candidate, top_path, _shape = triples[0]
    assert restroom.node_id in top_path


def test_fountain_fallback_when_no_restroom_available(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    fountain = _amenity_at(*FOUNTAIN_OFFSET, kind="fountain")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [fountain], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert triples
    candidate, _node_path, shape = triples[0]
    assert shape == "round"
    matches = match_amenities_to_route(candidate.geometry, [fountain.amenity])
    assert matches
    assert MIN_RANGE_M <= matches[0].mile_marker_m <= MAX_RANGE_M


def test_amenity_reachable_but_outside_requested_range_is_rejected(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    # A restroom close enough to reach, but the requested window is a
    # narrow sliver nowhere near where it actually lands on any loop --
    # must not be silently accepted just because it's ON the route.
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    impossible_min = TARGET_DISTANCE_M * 0.02
    impossible_max = TARGET_DISTANCE_M * 0.03

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom], impossible_min, impossible_max, COUNT,
    )

    assert triples == []


def test_no_amenities_returns_empty_gracefully(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    assert triples == []


# ---------------------------------------------------------------------------
# 14-16: boundary safety, determinism, count limit
# ---------------------------------------------------------------------------


def test_start_near_grid_corner_does_not_crash(grid_graph: nx.MultiDiGraph) -> None:
    corner_start = _node_id(2, 2)
    restroom = _amenity_at(4, 4, kind="restroom")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, corner_start), TARGET_DISTANCE_M,
        [restroom], MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert isinstance(triples, list)
    assert len(triples) <= COUNT


def test_deterministic_across_calls(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    start_coord = node_coordinate(grid_graph, start_node)

    first = polygon_loop_through_amenities_pairs(
        grid_graph, start_coord, TARGET_DISTANCE_M, [restroom],
        MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )
    second = polygon_loop_through_amenities_pairs(
        grid_graph, start_coord, TARGET_DISTANCE_M, [restroom],
        MIN_RANGE_M, MAX_RANGE_M, COUNT,
    )

    assert [path for _c, path, _s in first] == [path for _c, path, _s in second]


def test_never_returns_more_than_requested_count(
    grid_graph: nx.MultiDiGraph, start_node: int
) -> None:
    restroom_a = _amenity_at(*RESTROOM_OFFSET, kind="restroom")
    restroom_b = _amenity_at(GRID_CENTER_INDEX - 8, GRID_CENTER_INDEX + 6, kind="restroom")
    fountain = _amenity_at(*FOUNTAIN_OFFSET, kind="fountain")

    triples = polygon_loop_through_amenities_pairs(
        grid_graph, node_coordinate(grid_graph, start_node), TARGET_DISTANCE_M,
        [restroom_a, restroom_b, fountain], MIN_RANGE_M, MAX_RANGE_M, count=2,
    )

    assert len(triples) <= 2


# ---------------------------------------------------------------------------
# sanity on the module-level search-heuristic helpers
# ---------------------------------------------------------------------------


def test_radial_reach_margin_is_tighter_than_half_target() -> None:
    # Documents/pins the deliberate choice to use a loop-appropriate
    # reach bound instead of amenity_first.py's out-and-back "target/2".
    assert AMENITY_RADIAL_REACH_MARGIN < 0.5


def test_templates_all_have_valid_leg_fraction_partitions() -> None:
    from app.generation.polygon_amenity import _leg_fractions

    for template in TEMPLATES:
        fractions = _leg_fractions(template)
        assert set(fractions) == {"AB", "BC", "CD", "DA"}
        boundaries = [fractions[leg] for leg in ("AB", "BC", "CD", "DA")]
        assert boundaries[0][0] == 0.0
        assert boundaries[-1][1] == 1.0
        for (_start, end), (next_start, _next_end) in zip(boundaries, boundaries[1:]):
            assert end == next_start
