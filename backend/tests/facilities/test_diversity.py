from app.facilities.diversity import segment_overlap, select_diverse, route_segment_signature
from app.routing.provider import RoutePoint


def _line(lat_start: float, n: int) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat_start + i * 0.001, lon=-73.0, elevation_m=0.0) for i in range(n)
    )


def test_identical_geometry_full_overlap() -> None:
    geometry = _line(40.0, 10)
    sig_a = route_segment_signature(geometry)
    sig_b = route_segment_signature(geometry)
    assert segment_overlap(sig_a, sig_b) == 1.0


def test_disjoint_geometry_zero_overlap() -> None:
    sig_a = route_segment_signature(_line(40.0, 10))
    sig_b = route_segment_signature(_line(50.0, 10))
    assert segment_overlap(sig_a, sig_b) == 0.0


def test_select_diverse_never_returns_fewer_than_available() -> None:
    items = list(range(5))
    # All items map to the SAME geometry (max overlap) -- diversity can
    # never be satisfied, but count must still be filled from the ranked
    # pool.
    geometry = _line(40.0, 5)
    result = select_diverse(items, lambda _i: geometry, count=3)
    assert len(result) == 3


def test_select_diverse_prefers_distinct_geometry() -> None:
    # item 0 and 1 are near-identical (high overlap); item 2 is distinct.
    geometries = {0: _line(40.0, 10), 1: _line(40.0, 10), 2: _line(50.0, 10)}
    items = [0, 1, 2]
    result = select_diverse(items, lambda i: geometries[i], count=2)
    assert 0 in result and 2 in result
    assert 1 not in result  # redundant with 0, skipped in favor of diverse item 2


def test_select_diverse_preserves_rank_order_in_output() -> None:
    geometries = {0: _line(40.0, 10), 1: _line(41.0, 10), 2: _line(50.0, 10)}
    items = [0, 1, 2]
    result = select_diverse(items, lambda i: geometries[i], count=3)
    assert result == [0, 1, 2]


def test_select_diverse_returns_all_when_count_exceeds_pool() -> None:
    items = [0, 1]
    geometry = _line(40.0, 5)
    assert select_diverse(items, lambda _i: geometry, count=5) == [0, 1]
