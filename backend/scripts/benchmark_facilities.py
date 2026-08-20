"""New generic facility-routing benchmark suite (PR #18, Phase 7).

SEPARATE from `scripts/benchmark_suite.py`'s legacy 537-scenario
no-facility/single-fountain suite, which this script does not replace
or modify.

STRATUM A ("feasible by construction") only: every facility fixture is
synthetic, derived from a real reference route's own geometry at the
target cumulative mile marker (same technique validated in
`tests/test_canonical_scenarios.py`), guaranteeing genuine reachability
rather than depending on where real-world restrooms/water happen to
sit. This answers "can the planner correctly honor multiple typed
cumulative-mile stops," not "how often does current real facility
coverage support these asks" (STRATUM B, real-OSM-water-coverage
reporting, is out of scope for this pass -- restrooms are live-Supabase-
only and must not be represented as offline-real in a deterministic
report; a future pass could add a water-only stratum B using the
committed OSM dataset directly).

Axes: distance (5/8/12mi) x shape (round/out_and_back/mix) x
requirement count (0/1/2/4) x composition (restroom/water/mixed, for
count>=1) x 2 real Manhattan anchors. This is a deliberately smaller
matrix than the spec's full suggested axis set (distance also covers
10/15mi, shape also isolates OAB from mix, requirement count also
covers 6, composition axis repeats per count) -- scoped down to stay
tractable in one benchmark pass; the harness below is written so
widening any axis is a one-line change.

For each scenario, `plan_routes` is called TWICE: once with no
constrained planners (natural-match-only) and once with both (the
production default) -- the delta between the two directly measures how
often natural matching alone was sufficient vs. the constrained
planners were actually needed, without adding planner-internal
instrumentation to production code for this one report.
"""

import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.facilities.models import Facility, FacilityRequirement
from app.facilities.oab_planner import plan_constrained_out_and_back
from app.facilities.orchestration import plan_routes
from app.facilities.round_planner import plan_constrained_round
from app.generation.engine import generate_routes
from app.graph.loader import get_graph
from app.restrooms.geo import cumulative_distances_m
from app.routing.provider import Coordinate, RoutePoint


MI = 1609.34
RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "facilities"

Shape = Literal["round", "out_and_back", "mix"]
Composition = Literal["restroom", "water", "mixed"]

ANCHORS: tuple[tuple[str, float, float], ...] = (
    ("Central Park - Great Lawn", 40.7813, -73.9648),
    ("Upper West Side - 86th", 40.7870, -73.9754),
)

DISTANCES_MI: tuple[float, ...] = (5.0, 8.0, 12.0)
SHAPES: tuple[Shape, ...] = ("round", "out_and_back", "mix")
REQUIREMENT_COUNTS: tuple[int, ...] = (0, 1, 2, 4)
COMPOSITIONS: tuple[Composition, ...] = ("restroom", "water", "mixed")

# Requirement windows as (min_fraction, max_fraction) of target distance,
# by requirement count -- spread across the route so a real planner has
# to actually place multiple distinct stops, not cluster them.
WINDOWS_BY_COUNT: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.30, 0.50),),
    2: ((0.15, 0.35), (0.55, 0.75)),
    4: ((0.05, 0.20), (0.25, 0.40), (0.50, 0.65), (0.70, 0.85)),
}


def _point_at_mile(geometry: tuple[RoutePoint, ...], target_m: float) -> RoutePoint:
    distances = cumulative_distances_m(geometry)
    best_index, best_err = 0, float("inf")
    for index, distance in enumerate(distances):
        err = abs(distance - target_m)
        if err < best_err:
            best_err, best_index = err, index
    return geometry[best_index]


def _kinds_for(composition: Composition, count: int) -> list[Literal["restroom", "water"]]:
    if composition == "restroom":
        return ["restroom"] * count
    if composition == "water":
        return ["water"] * count
    return [("restroom" if i % 2 == 0 else "water") for i in range(count)]


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    distance_mi: float
    shape: Shape
    requirement_count: int
    composition: Composition | None
    ok: bool
    distance_error_m: float | None
    fully_valid: bool
    satisfied_count: int
    total_count: int
    natural_only_fully_valid: bool
    forced_planner_needed: bool
    latency_s: float


def _run_scenario(
    name: str,
    start: Coordinate,
    distance_mi: float,
    shape: Shape,
    requirement_count: int,
    composition: Composition | None,
) -> ScenarioResult:
    graph = get_graph()
    target_m = distance_mi * MI

    requirements: list[FacilityRequirement] = []
    facilities: list[Facility] = []

    if requirement_count > 0:
        reference_shape = "round" if shape == "mix" else shape
        reference = generate_routes(graph, start, target_m, reference_shape, 1)
        if not reference:
            return ScenarioResult(
                name, distance_mi, shape, requirement_count, composition,
                False, None, False, 0, requirement_count, False, False, 0.0,
            )
        geometry = reference[0].candidate.geometry
        windows = WINDOWS_BY_COUNT[requirement_count]
        kinds = _kinds_for(composition or "restroom", requirement_count)

        for index, ((lo_frac, hi_frac), kind) in enumerate(zip(windows, kinds)):
            min_m, max_m = lo_frac * target_m, hi_frac * target_m
            midpoint = (min_m + max_m) / 2.0
            point = _point_at_mile(geometry, midpoint)
            facility_id = f"{kind}:{index}"
            facilities.append(
                Facility(
                    id=facility_id, kind=kind, lat=point.lat, lon=point.lon,
                    name=facility_id, status="Operational" if kind == "restroom" else None,
                    hours_of_operation=None, source="benchmark",
                )
            )
            requirements.append(
                FacilityRequirement(
                    id=f"req{index}", kind=kind, min_distance_m=min_m, max_distance_m=max_m
                )
            )

    t0 = time.perf_counter()
    natural_only = plan_routes(
        graph, start, target_m, shape, 3, requirements, facilities,
        constrained_planners=None,
    )
    natural_fully_valid = any(s.fully_valid for s in natural_only)

    full = plan_routes(
        graph, start, target_m, shape, 3, requirements, facilities,
        constrained_planners=[plan_constrained_round, plan_constrained_out_and_back],
    )
    latency_s = time.perf_counter() - t0

    if not full:
        return ScenarioResult(
            name, distance_mi, shape, requirement_count, composition,
            False, None, False, 0, requirement_count, natural_fully_valid, False, latency_s,
        )

    top = full[0]
    return ScenarioResult(
        name=name, distance_mi=distance_mi, shape=shape,
        requirement_count=requirement_count, composition=composition,
        ok=True,
        distance_error_m=top.distance_error_m,
        fully_valid=top.fully_valid,
        satisfied_count=top.facility_score.requirements_satisfied_count,
        total_count=top.facility_score.requirements_total,
        natural_only_fully_valid=natural_fully_valid,
        forced_planner_needed=(not natural_fully_valid) and top.fully_valid,
        latency_s=latency_s,
    )


def _all_scenarios() -> list[tuple[str, Coordinate, float, Shape, int, Composition | None]]:
    scenarios: list[tuple[str, Coordinate, float, Shape, int, Composition | None]] = []
    for anchor_name, lat, lon in ANCHORS:
        start = Coordinate(lat=lat, lon=lon)
        for distance_mi in DISTANCES_MI:
            for shape in SHAPES:
                for count in REQUIREMENT_COUNTS:
                    if count == 0:
                        name = f"{anchor_name} | {distance_mi}mi | {shape} | 0req"
                        scenarios.append((name, start, distance_mi, shape, 0, None))
                        continue
                    for composition in COMPOSITIONS:
                        name = (
                            f"{anchor_name} | {distance_mi}mi | {shape} | "
                            f"{count}req | {composition}"
                        )
                        scenarios.append((name, start, distance_mi, shape, count, composition))
    return scenarios


def _bucket_for_count(count: int) -> str:
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3-4" if count <= 4 else "5-6"


def build_report(results: list[ScenarioResult]) -> str:
    lines: list[str] = []
    lines.append(f"# Facility-routing benchmark report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"Scenarios run: {len(results)}")
    ok_results = [r for r in results if r.ok]
    lines.append(f"Produced >=1 candidate: {len(ok_results)}/{len(results)}")
    lines.append("")

    lines.append("## Correctness by requirement-count bucket")
    lines.append("")
    lines.append("| bucket | scenarios | >=1 route | fully constraint-valid | per-requirement satisfaction | median latency | p95 latency |")
    lines.append("|---|---|---|---|---|---|---|")
    for bucket in ("0", "1", "2", "3-4"):
        bucket_results = [r for r in results if _bucket_for_count(r.requirement_count) == bucket]
        if not bucket_results:
            continue
        ok_count = sum(1 for r in bucket_results if r.ok)
        fully_valid_count = sum(1 for r in bucket_results if r.ok and r.fully_valid)
        satisfied = sum(r.satisfied_count for r in bucket_results if r.ok)
        total_req = sum(r.total_count for r in bucket_results if r.ok)
        latencies = [r.latency_s for r in bucket_results]
        median_lat = statistics.median(latencies) if latencies else 0.0
        p95_lat = (
            statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies, default=0.0)
        )
        req_rate = f"{100 * satisfied / total_req:.1f}%" if total_req else "n/a"
        lines.append(
            f"| {bucket} | {len(bucket_results)} | {ok_count}/{len(bucket_results)} | "
            f"{fully_valid_count}/{len(bucket_results)} | {req_rate} | "
            f"{median_lat:.2f}s | {p95_lat:.2f}s |"
        )
    lines.append("")

    lines.append("## Natural-match vs forced-planner")
    lines.append("")
    with_requirements = [r for r in results if r.ok and r.requirement_count > 0]
    natural_sufficient = sum(1 for r in with_requirements if r.natural_only_fully_valid)
    forced_needed_and_succeeded = sum(1 for r in with_requirements if r.forced_planner_needed)
    forced_needed_and_failed = sum(
        1 for r in with_requirements if not r.natural_only_fully_valid and not r.fully_valid
    )
    total_with_req = len(with_requirements)
    if total_with_req:
        lines.append(
            f"- Natural match alone sufficient: {natural_sufficient}/{total_with_req} "
            f"({100 * natural_sufficient / total_with_req:.1f}%)"
        )
        lines.append(
            f"- Constrained planner needed AND succeeded: {forced_needed_and_succeeded}/{total_with_req} "
            f"({100 * forced_needed_and_succeeded / total_with_req:.1f}%)"
        )
        lines.append(
            f"- Constrained planner needed but still not fully valid: {forced_needed_and_failed}/{total_with_req} "
            f"({100 * forced_needed_and_failed / total_with_req:.1f}%)"
        )
    lines.append("")

    lines.append("## Latency by requirement-count bucket (overall)")
    lines.append("")
    for bucket in ("0", "1", "2", "3-4"):
        bucket_results = [r for r in results if _bucket_for_count(r.requirement_count) == bucket]
        latencies = [r.latency_s for r in bucket_results]
        if not latencies:
            continue
        lines.append(
            f"- {bucket} requirements: median {statistics.median(latencies):.2f}s, "
            f"max {max(latencies):.2f}s (n={len(latencies)})"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    scenarios = _all_scenarios()
    print(f"Running {len(scenarios)} facility-routing scenarios...")

    results: list[ScenarioResult] = []
    for i, (name, start, distance_mi, shape, count, composition) in enumerate(scenarios):
        result = _run_scenario(name, start, distance_mi, shape, count, composition)
        results.append(result)
        if (i + 1) % 20 == 0 or i == len(scenarios) - 1:
            print(f"  ... {i + 1}/{len(scenarios)} done")

    report_text = build_report(results)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
