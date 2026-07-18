"""Phase 2 evidence benchmark for amenity-aware route generation.

Runs `generate_amenity_aware` against the REAL Manhattan graph and REAL
fountains for every scenario x shape (round, out_and_back), reusing the
start coordinates and target distances from scripts/benchmark_routes.py.
Requests a route with a fountain in range 1.0-2.0 miles.

No hard gate -- this is the Phase 2 proof-of-requirement. Reports the
% of (scenario, shape) requests producing a route that both passes a
fountain in range AND lands within +/-100m of target, plus median
latency.
"""

import statistics
import time
from dataclasses import dataclass

from app.amenities.fountains import fountain_to_amenity, get_fountains
from app.amenities.snapping import SnappedAmenity, amenities_in_range, snap_amenities
from app.generation.engine import Shape, generate_amenity_aware
from app.graph.distances import nearest_node, single_source_distances
from app.graph.loader import get_graph
from app.routing.provider import Coordinate, RouteCandidate
from scripts.benchmark_routes import SCENARIOS


MILES_TO_METERS = 1609.34
TOLERANCE_M = 100.0
COUNT = 5
SHAPES: tuple[Shape, ...] = ("round", "out_and_back")

MIN_RANGE_M = 1.0 * MILES_TO_METERS
MAX_RANGE_M = 2.0 * MILES_TO_METERS

MAX_SNAP_M = 200.0


@dataclass(frozen=True)
class RequestResult:
    scenario_name: str
    shape: str
    target_m: float
    passed_amenity_and_length: bool
    best_error_m: float | None
    candidate_count: int
    time_s: float


def _passes_amenity(
    candidate: RouteCandidate,
    graph: object,
    in_range_nodes: set[int],
) -> bool:
    """True if any vertex of the candidate's geometry coincides with an
    in-range amenity's snapped node coordinate."""
    from app.graph.model import node_coordinate

    route_points = {(round(p.lat, 6), round(p.lon, 6)) for p in candidate.geometry}

    for node_id in in_range_nodes:
        coord = node_coordinate(graph, node_id)
        if (round(coord.lat, 6), round(coord.lon, 6)) in route_points:
            return True

    return False


def run_request(
    graph: object,
    scenario_name: str,
    start: Coordinate,
    target_m: float,
    shape: Shape,
    snapped: list[SnappedAmenity],
) -> RequestResult:
    start_node = nearest_node(graph, start)
    dists = single_source_distances(graph, start_node)
    in_range = amenities_in_range(snapped, dists, MIN_RANGE_M, MAX_RANGE_M)
    in_range_nodes = {entry.node_id for entry in in_range}

    start_time = time.monotonic()
    candidates = generate_amenity_aware(
        graph,
        start,
        target_m,
        shape,
        COUNT,
        snapped,
        MIN_RANGE_M,
        MAX_RANGE_M,
    )
    elapsed = time.monotonic() - start_time

    passing = [
        c
        for c in candidates
        if abs(c.distance_m - target_m) <= TOLERANCE_M
        and _passes_amenity(c, graph, in_range_nodes)
    ]

    errors = [abs(c.distance_m - target_m) for c in candidates]

    return RequestResult(
        scenario_name=scenario_name,
        shape=shape,
        target_m=target_m,
        passed_amenity_and_length=bool(passing),
        best_error_m=min(errors) if errors else None,
        candidate_count=len(candidates),
        time_s=elapsed,
    )


def main() -> None:
    graph = get_graph()

    fountains = get_fountains()
    amenities = [fountain_to_amenity(f) for f in fountains]
    snapped = snap_amenities(graph, amenities, max_snap_m=MAX_SNAP_M)

    print(f"Loaded {len(fountains)} fountains, {len(snapped)} snapped within {MAX_SNAP_M:.0f}m\n")

    results: list[RequestResult] = []

    print(f"{'scenario':28s} {'shape':12s} {'pass':>5s} {'best_err_m':>10s} {'time_s':>7s}")
    print("-" * 68)

    for scenario in SCENARIOS:
        start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
        target_m = scenario.target_distance_miles * MILES_TO_METERS

        for shape in SHAPES:
            result = run_request(
                graph, scenario.name, start, target_m, shape, snapped
            )
            results.append(result)
            err = (
                f"{result.best_error_m:10.0f}"
                if result.best_error_m is not None
                else f"{'n/a':>10s}"
            )
            passed = "YES" if result.passed_amenity_and_length else "no"
            print(
                f"{scenario.name[:28]:28s} {shape:12s} {passed:>5s} {err} "
                f"{result.time_s:7.2f}"
            )

    passing = [r for r in results if r.passed_amenity_and_length]
    hit_rate = 100.0 * len(passing) / len(results) if results else 0.0
    median_time = statistics.median(r.time_s for r in results)

    print("\n=== Scorecard ===")
    print(f"Requests: {len(results)} ({len(SCENARIOS)} scenarios x {len(SHAPES)} shapes)")
    print(
        f"Passed (fountain in range 1.0-2.0mi AND within +/-{TOLERANCE_M:.0f}m): "
        f"{len(passing)}/{len(results)} ({hit_rate:.1f}%)"
    )
    print(f"Median request time: {median_time:.3f}s")

    by_shape: dict[str, list[RequestResult]] = {}
    for r in results:
        by_shape.setdefault(r.shape, []).append(r)

    print("\nBy shape:")
    for shape_name, shape_results in by_shape.items():
        shape_passing = [r for r in shape_results if r.passed_amenity_and_length]
        shape_pct = 100.0 * len(shape_passing) / len(shape_results)
        print(f"  {shape_name:12s} {len(shape_passing)}/{len(shape_results)} ({shape_pct:.1f}%)")


if __name__ == "__main__":
    main()
