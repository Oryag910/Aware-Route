"""No-facility count-reliability benchmark for the generic /routes path.

The historical 537-scenario suite (scripts/benchmark_suite.py) only ever
asserted ">=1 valid route" per scenario -- it never checked whether the
RETURNED candidate count matched the REQUESTED count, which is exactly
why the no-facility route-count bug (sector starvation in
app.generation.turnarounds.select_turnarounds -- see that module and
app.facilities.orchestration) shipped without being caught. This
benchmark closes that gap by exercising the real product code path,
app.facilities.orchestration.plan_routes with facility_requirements=[]
(not generate_candidates directly, which bypasses the no-facility
overcomplete-pool policy entirely), at both the product default
(count=3) and the API max (count=5), split by shape.

Writes a markdown report to benchmarks/local/count_reliability_<timestamp>.md.
Does not overwrite the historical 537-scenario report or its own prior runs.
"""

import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.facilities.diversity import route_segment_signature, segment_overlap
from app.facilities.orchestration import plan_routes
from app.graph.loader import get_graph
from app.routing.provider import Coordinate
from scripts.benchmark_scenarios import SCENARIOS, Scenario


MILES_TO_METERS = 1609.34
TOLERANCE_M = 100.0
COUNTS: tuple[int, ...] = (3, 5)
SHAPES: tuple[str, ...] = ("round", "out_and_back", "mix")

RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "local"


@dataclass(frozen=True)
class CountResult:
    scenario_name: str
    shape: str
    hard_case: bool
    requested_count: int
    returned_count: int
    exact_count_fulfilled: bool
    time_s: float
    within_tolerance_count: int
    all_within_tolerance: bool
    pairwise_overlaps: tuple[float, ...]
    error: str | None

    @property
    def exact_count_all_within_tolerance(self) -> bool:
        """The product-facing target: exactly `requested_count` routes
        returned AND every one of them within tolerance -- stronger than
        either metric alone (see PR discussion: exact-count fulfillment
        alone can hide a batch where extra alternatives miss distance)."""
        return self.exact_count_fulfilled and self.all_within_tolerance


def run_one(graph: Any, scenario: Scenario, count: int) -> CountResult:
    start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
    target_m = scenario.target_distance_miles * MILES_TO_METERS

    t0 = time.monotonic()
    try:
        scored = plan_routes(graph, start, target_m, scenario.shape, count, [], [])
        error = None
    except Exception as exc:  # noqa: BLE001 -- benchmark must survive any planner failure
        scored = []
        error = str(exc)
    elapsed = time.monotonic() - t0

    within = sum(
        1
        for s in scored
        if abs(s.route.candidate.distance_m - target_m) <= TOLERANCE_M
    )
    all_within = bool(scored) and within == len(scored)
    signatures = [route_segment_signature(s.route.candidate.geometry) for s in scored]
    overlaps = tuple(
        segment_overlap(signatures[i], signatures[j])
        for i in range(len(signatures))
        for j in range(i + 1, len(signatures))
    )

    return CountResult(
        scenario_name=scenario.name,
        shape=scenario.shape,
        hard_case=scenario.hard_case,
        requested_count=count,
        returned_count=len(scored),
        exact_count_fulfilled=len(scored) == count,
        time_s=elapsed,
        within_tolerance_count=within,
        all_within_tolerance=all_within,
        pairwise_overlaps=overlaps,
        error=error,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[index]


def _bucket_row(shape: str, bucket: list[CountResult]) -> str:
    exact_pct = 100.0 * sum(r.exact_count_fulfilled for r in bucket) / len(bucket)
    all_within_pct = 100.0 * sum(r.all_within_tolerance for r in bucket) / len(bucket)
    exact_and_all_within_pct = (
        100.0 * sum(r.exact_count_all_within_tolerance for r in bucket) / len(bucket)
    )
    median_returned = statistics.median(r.returned_count for r in bucket)
    ones = sum(1 for r in bucket if r.returned_count == 1)
    twos = sum(1 for r in bucket if r.returned_count == 2)

    total_candidates = sum(r.returned_count for r in bucket)
    total_within = sum(r.within_tolerance_count for r in bucket)
    within_rate = 100.0 * total_within / total_candidates if total_candidates else 0.0

    all_overlaps = [o for r in bucket for o in r.pairwise_overlaps]
    median_overlap_str = (
        f"{statistics.median(all_overlaps):.3f}" if all_overlaps else "n/a"
    )

    latencies = [r.time_s for r in bucket]
    median_latency = statistics.median(latencies)
    p95_latency = _percentile(latencies, 95.0)

    return (
        f"| {shape} | {len(bucket)} | {exact_pct:.1f}% | {all_within_pct:.1f}% | "
        f"{exact_and_all_within_pct:.1f}% | {median_returned:.1f} | "
        f"{ones} | {twos} | {within_rate:.1f}% | {median_overlap_str} | "
        f"{median_latency:.3f}s | {p95_latency:.3f}s |"
    )


def build_report(results: list[CountResult]) -> str:
    lines: list[str] = []
    lines.append(
        f"# No-facility count-reliability benchmark — "
        f"{datetime.now().isoformat(timespec='seconds')}"
    )
    lines.append("")
    lines.append(
        "Fills the blind spot in the historical 537-scenario suite "
        "(scripts/benchmark_suite.py), whose only success criterion was "
        "\">=1 valid route\" -- it never asserted the RETURNED count "
        "matched the REQUESTED count. This exercises the real product "
        "code path (`app.facilities.orchestration.plan_routes` with "
        "`facility_requirements=[]`), not `generate_candidates` directly, "
        "which bypasses the no-facility overcomplete-pool policy "
        "entirely (see `app.facilities.orchestration.natural_match_pool`)."
    )
    lines.append("")

    errors = [r for r in results if r.error is not None]
    if errors:
        lines.append(f"## Errors ({len(errors)})")
        lines.append("")
        for r in errors:
            lines.append(f"- {r.scenario_name} (count={r.requested_count}): {r.error}")
        lines.append("")

    for count in COUNTS:
        bucket_all = [
            r for r in results if r.requested_count == count and r.error is None
        ]
        lines.append(f"## requested count = {count}")
        lines.append("")
        lines.append(
            "| shape | scenarios | exact-count % | all-returned-within-100m % | "
            "exact-count AND all-within-100m % | median returned | "
            "returned==1 | returned==2 | candidate within-100m rate | median overlap | "
            "median latency | p95 latency |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for shape in (*SHAPES, "ALL"):
            bucket = (
                bucket_all if shape == "ALL" else [r for r in bucket_all if r.shape == shape]
            )
            if not bucket:
                continue
            lines.append(_bucket_row(shape, bucket))
        lines.append("")

    hard_results = [r for r in results if r.hard_case]
    if hard_results:
        lines.append("## Known hard-case detail")
        lines.append("")
        lines.append("| scenario | shape | requested | returned | time |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(hard_results, key=lambda r: (r.scenario_name, r.requested_count)):
            lines.append(
                f"| {r.scenario_name} | {r.shape} | {r.requested_count} | "
                f"{r.returned_count} | {r.time_s:.3f}s |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    graph = get_graph()
    print(f"Running {len(SCENARIOS)} scenarios x {len(COUNTS)} counts ...\n")

    results: list[CountResult] = []
    total = len(SCENARIOS) * len(COUNTS)
    done = 0
    for scenario in SCENARIOS:
        for count in COUNTS:
            results.append(run_one(graph, scenario, count))
            done += 1
            if done % 200 == 0 or done == total:
                print(f"  ... {done}/{total} done")

    report_text = build_report(results)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"count_reliability_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
