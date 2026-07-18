"""Phase 1 gate benchmark for the in-process routing engine.

Runs `generate_candidates` against the REAL Manhattan graph for every
scenario x shape (round, out_and_back), reusing the start coordinates
and target distances from scripts/benchmark_routes.py. Records the best
absolute distance error vs target and wall-clock time per request, then
prints a scorecard.

GATE: >=90% of (scenario, shape) requests have a candidate within +/-100m
of target, and median request time < 1s.
"""

import statistics
import time
from dataclasses import dataclass

from app.generation.engine import Shape, generate_candidates
from app.graph.loader import get_graph
from app.routing.provider import Coordinate
from scripts.benchmark_routes import SCENARIOS


MILES_TO_METERS = 1609.34
TOLERANCE_M = 100.0
COUNT = 5
SHAPES: tuple[Shape, ...] = ("round", "out_and_back")

GATE_WITHIN_PCT = 90.0
GATE_MEDIAN_S = 1.0


@dataclass(frozen=True)
class RequestResult:
    scenario_name: str
    shape: str
    target_m: float
    best_error_m: float | None
    candidate_count: int
    time_s: float


def run_request(
    graph: object,
    scenario_name: str,
    start: Coordinate,
    target_m: float,
    shape: Shape,
) -> RequestResult:
    start_time = time.monotonic()
    candidates = generate_candidates(graph, start, target_m, shape, COUNT)
    elapsed = time.monotonic() - start_time

    errors = [abs(c.distance_m - target_m) for c in candidates]
    return RequestResult(
        scenario_name=scenario_name,
        shape=shape,
        target_m=target_m,
        best_error_m=min(errors) if errors else None,
        candidate_count=len(candidates),
        time_s=elapsed,
    )


def main() -> None:
    graph = get_graph()
    results: list[RequestResult] = []

    print("Running local-graph benchmark ...\n")
    print(f"{'scenario':28s} {'shape':12s} {'best_err_m':>10s} {'time_s':>7s}")
    print("-" * 60)

    for scenario in SCENARIOS:
        start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
        target_m = scenario.target_distance_miles * MILES_TO_METERS

        for shape in SHAPES:
            result = run_request(
                graph, scenario.name, start, target_m, shape
            )
            results.append(result)
            err = (
                f"{result.best_error_m:10.0f}"
                if result.best_error_m is not None
                else f"{'n/a':>10s}"
            )
            print(
                f"{scenario.name[:28]:28s} {shape:12s} {err} "
                f"{result.time_s:7.2f}"
            )

    within = [
        r
        for r in results
        if r.best_error_m is not None and r.best_error_m <= TOLERANCE_M
    ]
    within_pct = 100.0 * len(within) / len(results) if results else 0.0
    median_time = statistics.median(r.time_s for r in results)

    print("\n=== Scorecard ===")
    print(f"Requests: {len(results)} ({len(SCENARIOS)} scenarios x {len(SHAPES)} shapes)")
    print(
        f"Within +/-{TOLERANCE_M:.0f}m: {len(within)}/{len(results)} "
        f"({within_pct:.1f}%)"
    )
    print(f"Median request time: {median_time:.3f}s")

    gate_accuracy = within_pct >= GATE_WITHIN_PCT
    gate_latency = median_time < GATE_MEDIAN_S
    passed = gate_accuracy and gate_latency

    print("\n=== Gate ===")
    print(
        f"  accuracy >= {GATE_WITHIN_PCT:.0f}%:  "
        f"{'PASS' if gate_accuracy else 'FAIL'} ({within_pct:.1f}%)"
    )
    print(
        f"  median   <  {GATE_MEDIAN_S:.0f}s:   "
        f"{'PASS' if gate_latency else 'FAIL'} ({median_time:.3f}s)"
    )
    print(f"\nVERDICT: {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
