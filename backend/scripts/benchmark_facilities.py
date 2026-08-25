"""New generic facility-routing benchmark suite (PR #18, Phase 7).

SEPARATE from `scripts/benchmark_suite.py`'s legacy 537-scenario
no-facility/single-fountain suite, which this script does not replace
or modify.

## Latency methodology (corrected -- see git history for the prior,
## flawed double-call version)

Each scenario's PRIMARY latency sample is exactly ONE end-to-end call to
`plan_routes` with the production-default constrained planners
(`PRODUCTION_PLANNERS`) -- this is what a real `POST /routes` request
actually pays. Every latency statistic in this report's "Correctness by
requirement-count bucket" and "Latency by requirement-count bucket"
sections is built from that single sample per scenario, never summed
with any other call.

A SEPARATE, clearly-labeled diagnostic call (natural-match-only, i.e.
`constrained_planners=None`) is used ONLY to compute the "natural match
alone sufficient" statistic in the "Natural-match vs forced-planner"
section. Its latency is reported separately, under its own heading, and
is explicitly excluded from the primary per-request latency figures.

## Strata

- **Stratum A (mechanism/correctness)**: facility fixtures derived from
  a real reference route's own geometry at the target mile marker --
  proves the matching/assignment/planner MECHANISM can honor a given
  cumulative-mile stop. Because these fixtures sit on or very near an
  ordinary candidate's own path, natural matching is often already
  sufficient here BY CONSTRUCTION -- this stratum's "natural match rate"
  must not be read as a general product-level prediction (see Stratum B).

- **Stratum B (planner-stress)**: facility fixtures placed at real graph
  nodes near the requested cumulative-mile target's RADIAL (shortest-
  path-from-start) distance instead of on an ordinary candidate's own
  geometry, and explicitly rejected/re-picked if any candidate in an
  ordinary natural pool already happens to encounter them in-window.
  This directly measures whether the constrained planners recover
  requirements that natural matching genuinely could not -- the number
  this repo should actually cite when asked "do the new planners pull
  their weight."

- **Stratum C (real water coverage)**: uses the committed OSM water
  dataset AS-IS (no synthetic placement), deterministic starts, water-
  only requirements. Answers "given ACTUAL committed water coverage, how
  often can a request be satisfied" -- a coverage measurement, not a
  correctness gate; low numbers here reflect real geography, not a bug.
  Restrooms are live-Supabase-only and are deliberately NOT included in
  any real-data stratum.

Axis choices below are deliberately smaller than the spec's full
suggested matrix to keep one benchmark pass tractable -- documented
inline per stratum.
"""

import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.amenities.fountains import get_fountains
from app.facilities.catalog import fountain_to_facility
from app.facilities.encounters import find_facility_encounters
from app.facilities.models import Facility, FacilityRequirement
from app.facilities.oab_planner import plan_constrained_out_and_back
from app.facilities.orchestration import ConstrainedPlanner, ScoredRoute, natural_match_pool, plan_routes
from app.facilities.round_planner import plan_constrained_round
from app.generation.engine import generate_routes
from app.graph.distances import nearest_node, single_source_paths
from app.graph.loader import get_graph
from app.graph.model import node_coordinate
from app.restrooms.geo import cumulative_distances_m
from app.routing.provider import Coordinate, RoutePoint


MI = 1609.34
RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "facilities"

Shape = Literal["round", "out_and_back", "mix"]
Composition = Literal["restroom", "water", "mixed"]

# The exact planner set /routes registers by default (app/main.py's
# DEFAULT_CONSTRAINED_PLANNERS) -- kept in sync manually since this is a
# standalone script, not an import of main.py (which would pull in
# FastAPI app construction for no reason).
PRODUCTION_PLANNERS: list[ConstrainedPlanner] = [plan_constrained_round, plan_constrained_out_and_back]

ANCHOR_A: tuple[str, float, float] = ("Central Park - Great Lawn", 40.7813, -73.9648)
ANCHORS_C: tuple[tuple[str, float, float], ...] = (
    ("Central Park - Great Lawn", 40.7813, -73.9648),
    ("Upper West Side - 86th", 40.7870, -73.9754),
)

# Stratum A: single anchor (kept to one, not the prior two, so adding
# the new 6-requirement bucket stays tractable in one pass -- coverage
# breadth trade-off documented here, not hidden).
DISTANCES_MI: tuple[float, ...] = (5.0, 8.0, 12.0)
SHAPES: tuple[Shape, ...] = ("round", "out_and_back", "mix")
REQUIREMENT_COUNTS: tuple[int, ...] = (0, 1, 2, 4, 6)
COMPOSITIONS: tuple[Composition, ...] = ("restroom", "water", "mixed")

# Stratum B: fewer distances/counts than A -- stress fixtures require an
# extra rejection search per requirement (see _stress_facility), so this
# stays a "focused deterministic matrix," per the review instruction,
# not a full cross-product.
STRESS_DISTANCES_MI: tuple[float, ...] = (8.0, 12.0)
STRESS_REQUIREMENT_COUNTS: tuple[int, ...] = (1, 2, 4, 6)

# Stratum C: water-only, mix shape only (mix already spans both
# concrete shapes internally), small deterministic sample.
WATER_COVERAGE_DISTANCES_MI: tuple[float, ...] = (5.0, 8.0, 12.0)
WATER_COVERAGE_REQUIREMENT_COUNTS: tuple[int, ...] = (1, 2)

# Requirement windows as (min_fraction, max_fraction) of target distance.
WINDOWS_BY_COUNT: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.30, 0.50),),
    2: ((0.15, 0.35), (0.55, 0.75)),
    4: ((0.05, 0.20), (0.25, 0.40), (0.50, 0.65), (0.70, 0.85)),
    6: (
        (0.05, 0.15), (0.20, 0.30), (0.35, 0.45),
        (0.50, 0.60), (0.65, 0.75), (0.80, 0.90),
    ),
}

# Bounded search width for Stratum B's per-window rejection search --
# how many candidate graph nodes (ranked by closeness to the window's
# radial target) get tried before giving up on that window.
STRESS_CANDIDATE_SEARCH_WIDTH = 40


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
    stratum: Literal["A", "B", "C"]
    distance_mi: float
    shape: Shape
    requirement_count: int
    composition: Composition | None
    ok: bool
    distance_error_m: float | None
    fully_valid: bool
    satisfied_count: int
    total_count: int
    # PRIMARY metric: exactly one production-representative plan_routes
    # call (constrained planners on, same as a real /routes request).
    latency_s: float
    # Diagnostic only -- excluded from every primary latency statistic.
    natural_only_fully_valid: bool | None
    natural_only_latency_s: float | None
    forced_planner_needed: bool
    forced_planner_recovered: bool  # Stratum B: planner satisfied a requirement natural matching could not


def _reference_placed_requirements(
    graph: object,
    start: Coordinate,
    target_m: float,
    shape: Shape,
    requirement_count: int,
    composition: Composition,
) -> tuple[list[FacilityRequirement], list[Facility]] | None:
    """Stratum A: facility fixtures at the exact point a real reference
    route's own geometry passes at each window's midpoint mile marker."""
    reference_shape = "round" if shape == "mix" else shape
    reference = generate_routes(graph, start, target_m, reference_shape, 1)
    if not reference:
        return None
    geometry = reference[0].candidate.geometry

    windows = WINDOWS_BY_COUNT[requirement_count]
    kinds = _kinds_for(composition, requirement_count)
    requirements: list[FacilityRequirement] = []
    facilities: list[Facility] = []

    for index, ((lo_frac, hi_frac), kind) in enumerate(zip(windows, kinds)):
        min_m, max_m = lo_frac * target_m, hi_frac * target_m
        midpoint = (min_m + max_m) / 2.0
        point = _point_at_mile(geometry, midpoint)
        facility_id = f"{kind}:ref:{index}"
        facilities.append(
            Facility(
                id=facility_id, kind=kind, lat=point.lat, lon=point.lon,
                name=facility_id, status="Operational" if kind == "restroom" else None,
                hours_of_operation=None, source="benchmark-reference",
            )
        )
        requirements.append(
            FacilityRequirement(id=f"req{index}", kind=kind, min_distance_m=min_m, max_distance_m=max_m)
        )

    return requirements, facilities


def _stress_facility(
    graph: object,
    dists: dict[int, float],
    natural_geometries: list[tuple[RoutePoint, ...]],
    target_m: float,
    min_frac: float,
    max_frac: float,
    kind: Literal["restroom", "water"],
    index: int,
) -> Facility | None:
    """Stratum B: a real graph node near the window's RADIAL (shortest-
    path-from-start) target distance, rejected if any candidate in an
    ordinary natural pool already encounters it within the window --
    i.e. a fixture the constrained planner genuinely has to work for,
    not one that rides along on an already-generated route's own path."""
    min_m, max_m = min_frac * target_m, max_frac * target_m
    midpoint = (min_m + max_m) / 2.0

    candidates = sorted(
        ((distance, node) for node, distance in dists.items() if distance > 0),
        key=lambda entry: abs(entry[0] - midpoint),
    )[:STRESS_CANDIDATE_SEARCH_WIDTH]

    for _distance, node in candidates:
        coord = node_coordinate(graph, node)
        candidate_facility = Facility(
            id=f"{kind}:stress:{index}", kind=kind, lat=coord.lat, lon=coord.lon,
            name=None, status="Operational" if kind == "restroom" else None,
            hours_of_operation=None, source="benchmark-stress",
        )
        already_covered = any(
            any(
                min_m <= encounter.mile_marker_m <= max_m
                for encounter in find_facility_encounters(geometry, [candidate_facility])
            )
            for geometry in natural_geometries
        )
        if not already_covered:
            return candidate_facility

    return None


def _stress_placed_requirements(
    graph: object,
    start: Coordinate,
    target_m: float,
    shape: Shape,
    requirement_count: int,
    composition: Composition,
) -> tuple[list[FacilityRequirement], list[Facility]] | None:
    start_node = nearest_node(graph, start)
    dists, _paths = single_source_paths(graph, start_node)

    natural_pool = natural_match_pool(graph, start, target_m, shape, 8, [])
    natural_geometries = [route.candidate.geometry for route in natural_pool]

    windows = WINDOWS_BY_COUNT[requirement_count]
    kinds = _kinds_for(composition, requirement_count)
    requirements: list[FacilityRequirement] = []
    facilities: list[Facility] = []

    for index, ((lo_frac, hi_frac), kind) in enumerate(zip(windows, kinds)):
        facility = _stress_facility(
            graph, dists, natural_geometries, target_m, lo_frac, hi_frac, kind, index
        )
        if facility is None:
            return None  # no genuinely-stressing candidate in this band -- skip scenario, don't fake one
        facilities.append(facility)
        requirements.append(
            FacilityRequirement(
                id=f"req{index}", kind=kind,
                min_distance_m=lo_frac * target_m, max_distance_m=hi_frac * target_m,
            )
        )

    return requirements, facilities


def _run_scenario(
    name: str,
    stratum: Literal["A", "B"],
    start: Coordinate,
    distance_mi: float,
    shape: Shape,
    requirement_count: int,
    composition: Composition | None,
) -> ScenarioResult | None:
    graph = get_graph()
    target_m = distance_mi * MI

    requirements: list[FacilityRequirement] = []
    facilities: list[Facility] = []

    if requirement_count > 0:
        assert composition is not None
        built = (
            _reference_placed_requirements(graph, start, target_m, shape, requirement_count, composition)
            if stratum == "A"
            else _stress_placed_requirements(graph, start, target_m, shape, requirement_count, composition)
        )
        if built is None:
            return None  # scenario skipped (reference route or stress fixture unavailable), not counted
        requirements, facilities = built

    # PRIMARY measurement: exactly ONE production-representative call.
    t0 = time.perf_counter()
    full = plan_routes(
        graph, start, target_m, shape, 3, requirements, facilities,
        constrained_planners=PRODUCTION_PLANNERS,
    )
    latency_s = time.perf_counter() - t0

    # Diagnostic-only second call, timed and reported SEPARATELY.
    natural_only_fully_valid: bool | None = None
    natural_only_latency_s: float | None = None
    if requirement_count > 0:
        t1 = time.perf_counter()
        natural_only: list[ScoredRoute] = plan_routes(
            graph, start, target_m, shape, 3, requirements, facilities,
            constrained_planners=None,
        )
        natural_only_latency_s = time.perf_counter() - t1
        natural_only_fully_valid = any(s.fully_valid for s in natural_only)

    if not full:
        return ScenarioResult(
            name=name, stratum=stratum, distance_mi=distance_mi, shape=shape,
            requirement_count=requirement_count, composition=composition,
            ok=False, distance_error_m=None, fully_valid=False,
            satisfied_count=0, total_count=requirement_count,
            latency_s=latency_s,
            natural_only_fully_valid=natural_only_fully_valid,
            natural_only_latency_s=natural_only_latency_s,
            forced_planner_needed=False, forced_planner_recovered=False,
        )

    top = full[0]
    forced_planner_needed = requirement_count > 0 and not bool(natural_only_fully_valid)
    forced_planner_recovered = forced_planner_needed and top.fully_valid

    return ScenarioResult(
        name=name, stratum=stratum, distance_mi=distance_mi, shape=shape,
        requirement_count=requirement_count, composition=composition,
        ok=True,
        distance_error_m=top.distance_error_m,
        fully_valid=top.fully_valid,
        satisfied_count=top.facility_score.requirements_satisfied_count,
        total_count=top.facility_score.requirements_total,
        latency_s=latency_s,
        natural_only_fully_valid=natural_only_fully_valid,
        natural_only_latency_s=natural_only_latency_s,
        forced_planner_needed=forced_planner_needed,
        forced_planner_recovered=forced_planner_recovered,
    )


def _run_water_coverage_scenario(
    name: str, start: Coordinate, distance_mi: float, requirement_count: int,
) -> ScenarioResult:
    """Stratum C: real committed water dataset, as-is, no synthetic
    placement, no rejection search -- just measures actual coverage."""
    graph = get_graph()
    target_m = distance_mi * MI

    fountains = get_fountains()
    facilities = [fountain_to_facility(f) for f in fountains]

    windows = WINDOWS_BY_COUNT[requirement_count]
    requirements = [
        FacilityRequirement(
            id=f"req{index}", kind="water",
            min_distance_m=lo_frac * target_m, max_distance_m=hi_frac * target_m,
        )
        for index, (lo_frac, hi_frac) in enumerate(windows)
    ]

    t0 = time.perf_counter()
    full = plan_routes(
        graph, start, target_m, "mix", 3, requirements, facilities,
        constrained_planners=PRODUCTION_PLANNERS,
    )
    latency_s = time.perf_counter() - t0

    if not full:
        return ScenarioResult(
            name=name, stratum="C", distance_mi=distance_mi, shape="mix",
            requirement_count=requirement_count, composition="water",
            ok=False, distance_error_m=None, fully_valid=False,
            satisfied_count=0, total_count=requirement_count, latency_s=latency_s,
            natural_only_fully_valid=None, natural_only_latency_s=None,
            forced_planner_needed=False, forced_planner_recovered=False,
        )

    top = full[0]
    return ScenarioResult(
        name=name, stratum="C", distance_mi=distance_mi, shape="mix",
        requirement_count=requirement_count, composition="water",
        ok=True,
        distance_error_m=top.distance_error_m, fully_valid=top.fully_valid,
        satisfied_count=top.facility_score.requirements_satisfied_count,
        total_count=top.facility_score.requirements_total,
        latency_s=latency_s,
        natural_only_fully_valid=None, natural_only_latency_s=None,
        forced_planner_needed=False, forced_planner_recovered=False,
    )


def _stratum_a_scenarios() -> list[tuple[str, Coordinate, float, Shape, int, Composition | None]]:
    anchor_name, lat, lon = ANCHOR_A
    start = Coordinate(lat=lat, lon=lon)
    scenarios: list[tuple[str, Coordinate, float, Shape, int, Composition | None]] = []
    for distance_mi in DISTANCES_MI:
        for shape in SHAPES:
            for count in REQUIREMENT_COUNTS:
                if count == 0:
                    scenarios.append((f"A | {anchor_name} | {distance_mi}mi | {shape} | 0req", start, distance_mi, shape, 0, None))
                    continue
                for composition in COMPOSITIONS:
                    name = f"A | {anchor_name} | {distance_mi}mi | {shape} | {count}req | {composition}"
                    scenarios.append((name, start, distance_mi, shape, count, composition))
    return scenarios


def _stratum_b_scenarios() -> list[tuple[str, Coordinate, float, Shape, int, Composition | None]]:
    anchor_name, lat, lon = ANCHOR_A
    start = Coordinate(lat=lat, lon=lon)
    scenarios: list[tuple[str, Coordinate, float, Shape, int, Composition | None]] = []
    for distance_mi in STRESS_DISTANCES_MI:
        for shape in SHAPES:
            for count in STRESS_REQUIREMENT_COUNTS:
                for composition in COMPOSITIONS:
                    name = f"B | {anchor_name} | {distance_mi}mi | {shape} | {count}req | {composition}"
                    scenarios.append((name, start, distance_mi, shape, count, composition))
    return scenarios


def _stratum_c_scenarios() -> list[tuple[str, Coordinate, float, int]]:
    scenarios: list[tuple[str, Coordinate, float, int]] = []
    for anchor_name, lat, lon in ANCHORS_C:
        start = Coordinate(lat=lat, lon=lon)
        for distance_mi in WATER_COVERAGE_DISTANCES_MI:
            for count in WATER_COVERAGE_REQUIREMENT_COUNTS:
                name = f"C | {anchor_name} | {distance_mi}mi | {count}req | water"
                scenarios.append((name, start, distance_mi, count))
    return scenarios


def _bucket_for_count(count: int) -> str:
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    if count <= 4:
        return "3-4"
    return "5-6"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) < 20:
        return max(values)
    index = min(len(values) - 1, int(len(values) * pct))
    return sorted(values)[index]


def _correctness_table(results: list[ScenarioResult]) -> list[str]:
    lines = [
        "| bucket | scenarios | >=1 route | fully constraint-valid | per-requirement satisfaction | median latency | p95 latency | max latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for bucket in ("0", "1", "2", "3-4", "5-6"):
        bucket_results = [r for r in results if _bucket_for_count(r.requirement_count) == bucket]
        if not bucket_results:
            continue
        ok_count = sum(1 for r in bucket_results if r.ok)
        fully_valid_count = sum(1 for r in bucket_results if r.ok and r.fully_valid)
        satisfied = sum(r.satisfied_count for r in bucket_results if r.ok)
        total_req = sum(r.total_count for r in bucket_results if r.ok)
        latencies = [r.latency_s for r in bucket_results]
        median_lat = statistics.median(latencies) if latencies else 0.0
        p95_lat = _percentile(latencies, 0.95)
        max_lat = max(latencies, default=0.0)
        req_rate = f"{100 * satisfied / total_req:.1f}%" if total_req else "n/a"
        lines.append(
            f"| {bucket} | {len(bucket_results)} | {ok_count}/{len(bucket_results)} | "
            f"{fully_valid_count}/{len(bucket_results)} | {req_rate} | "
            f"{median_lat:.2f}s | {p95_lat:.2f}s | {max_lat:.2f}s |"
        )
    return lines


def build_report(stratum_a: list[ScenarioResult], stratum_b: list[ScenarioResult], stratum_c: list[ScenarioResult]) -> str:
    lines: list[str] = []
    lines.append(f"# Facility-routing benchmark report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(
        "**Latency methodology**: every latency figure below is exactly ONE "
        "end-to-end `plan_routes` call with the production-default "
        "constrained planners (`PRODUCTION_PLANNERS`) -- the same call a "
        "real `POST /routes` request makes. A separate natural-match-only "
        "diagnostic call (used only for the natural-match-rate statistics) "
        "is timed and reported independently under its own heading and is "
        "never included in these figures."
    )
    lines.append("")
    lines.append(
        "**Reading the natural-match-rate statistics**: `plan_routes` is "
        "always called with `count=3` (the API default), and it only skips "
        "the constrained planners when natural matching ALONE already "
        "produced 3 fully-valid candidates -- not just 1. The "
        "\"natural match alone sufficient\" percentages below answer a "
        "narrower, still-useful question: did at least one fully-valid "
        "candidate exist naturally. The PRIMARY latency figures reflect "
        "real production behavior either way (constrained planners run "
        "whenever fewer than `count` fully-valid natural candidates were "
        "found, exactly like a live request), so a scenario can show "
        "\"natural match sufficient\" in the diagnostic stat while its "
        "primary latency still includes constrained-planner cost."
    )
    lines.append("")

    lines.append("## Stratum A -- mechanism/correctness (feasible-by-construction, route-derived fixtures)")
    lines.append("")
    lines.append(f"Scenarios run: {len(stratum_a)}. Produced >=1 candidate: {sum(1 for r in stratum_a if r.ok)}/{len(stratum_a)}.")
    lines.append("")
    lines.extend(_correctness_table(stratum_a))
    lines.append("")

    with_req_a = [r for r in stratum_a if r.ok and r.requirement_count > 0]
    natural_sufficient_a = sum(1 for r in with_req_a if r.natural_only_fully_valid)
    if with_req_a:
        lines.append(
            f"**Stratum A natural-match rate (fixtures ARE route-derived -- do not read this as a "
            f"general product-level prediction, see Stratum B below)**: natural matching alone "
            f"sufficient in {natural_sufficient_a}/{len(with_req_a)} "
            f"({100 * natural_sufficient_a / len(with_req_a):.1f}%) scenarios."
        )
        diag_latencies_a = [r.natural_only_latency_s for r in with_req_a if r.natural_only_latency_s is not None]
        if diag_latencies_a:
            lines.append(
                f"Diagnostic natural-only-call latency (NOT part of the primary latency figures above): "
                f"median {statistics.median(diag_latencies_a):.2f}s, max {max(diag_latencies_a):.2f}s."
            )
        lines.append("")

    lines.append("## Stratum B -- planner-stress (radial-distance-placed fixtures, rejected if naturally covered)")
    lines.append("")
    lines.append(
        f"Scenarios attempted: {len(stratum_b)}. A scenario is only counted here if a genuinely "
        f"stressing fixture (not already encountered by an 8-candidate natural pool) was found for "
        f"EVERY requirement window -- this is what proves the constrained planners are doing real "
        f"work, not riding along on an already-satisfying natural route."
    )
    lines.append("")
    lines.extend(_correctness_table(stratum_b))
    lines.append("")

    with_req_b = [r for r in stratum_b if r.ok and r.requirement_count > 0]
    natural_sufficient_b = sum(1 for r in with_req_b if r.natural_only_fully_valid)
    forced_recovered_b = sum(1 for r in with_req_b if r.forced_planner_recovered)
    forced_failed_b = sum(1 for r in with_req_b if r.forced_planner_needed and not r.forced_planner_recovered)
    if with_req_b:
        lines.append(
            f"**By construction, natural matching alone was sufficient in only "
            f"{natural_sufficient_b}/{len(with_req_b)} "
            f"({100 * natural_sufficient_b / len(with_req_b):.1f}%) of these stress scenarios** "
            f"(fixtures were specifically rejected when a natural pool already covered them)."
        )
        lines.append(
            f"- Constrained planner needed AND recovered full validity: {forced_recovered_b}/{len(with_req_b)} "
            f"({100 * forced_recovered_b / len(with_req_b):.1f}%)"
        )
        lines.append(
            f"- Constrained planner needed but still fell short: {forced_failed_b}/{len(with_req_b)} "
            f"({100 * forced_failed_b / len(with_req_b):.1f}%)"
        )
        diag_latencies_b = [r.natural_only_latency_s for r in with_req_b if r.natural_only_latency_s is not None]
        if diag_latencies_b:
            lines.append(
                f"Diagnostic natural-only-call latency (NOT part of the primary latency figures above): "
                f"median {statistics.median(diag_latencies_b):.2f}s, max {max(diag_latencies_b):.2f}s."
            )
        lines.append("")

    lines.append("## Stratum C -- real committed water-dataset coverage (no synthetic placement)")
    lines.append("")
    lines.append(
        "Uses `app.amenities.fountains.get_fountains()` (the committed OSM water extract) exactly "
        "as a live request would -- no synthetic fixtures, no rejection search. This is a coverage "
        "measurement, not a correctness gate: a low number reflects real geography around these "
        "starts/windows, not a bug."
    )
    lines.append("")
    fully_valid_c = sum(1 for r in stratum_c if r.ok and r.fully_valid)
    satisfied_c = sum(r.satisfied_count for r in stratum_c if r.ok)
    total_c = sum(r.total_count for r in stratum_c if r.ok)
    lines.append(f"Scenarios run: {len(stratum_c)}.")
    lines.append(f"- Fully constraint-valid (all requested water stops actually found): {fully_valid_c}/{len(stratum_c)}")
    lines.append(f"- Per-requirement satisfaction: {satisfied_c}/{total_c} ({100 * satisfied_c / total_c:.1f}%)" if total_c else "- Per-requirement satisfaction: n/a")
    latencies_c = [r.latency_s for r in stratum_c if r.ok]
    if latencies_c:
        lines.append(f"- Latency: median {statistics.median(latencies_c):.2f}s, max {max(latencies_c):.2f}s")
    lines.append("")
    lines.append("Per-scenario detail:")
    for r in stratum_c:
        lines.append(
            f"- {r.name}: {'ok' if r.ok else 'no candidate'}, "
            f"{r.satisfied_count}/{r.total_count} satisfied, {r.latency_s:.2f}s"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    stratum_a_specs = _stratum_a_scenarios()
    stratum_b_specs = _stratum_b_scenarios()
    stratum_c_specs = _stratum_c_scenarios()

    print(f"Stratum A: {len(stratum_a_specs)} scenarios")
    print(f"Stratum B: {len(stratum_b_specs)} scenarios")
    print(f"Stratum C: {len(stratum_c_specs)} scenarios")

    stratum_a: list[ScenarioResult] = []
    for i, (name, start, distance_mi, shape, count, composition) in enumerate(stratum_a_specs):
        result = _run_scenario(name, "A", start, distance_mi, shape, count, composition)
        if result is not None:
            stratum_a.append(result)
        if (i + 1) % 20 == 0 or i == len(stratum_a_specs) - 1:
            print(f"  A: {i + 1}/{len(stratum_a_specs)} done")

    stratum_b: list[ScenarioResult] = []
    for i, (name, start, distance_mi, shape, count, composition) in enumerate(stratum_b_specs):
        result = _run_scenario(name, "B", start, distance_mi, shape, count, composition)
        if result is not None:
            stratum_b.append(result)
        if (i + 1) % 10 == 0 or i == len(stratum_b_specs) - 1:
            print(f"  B: {i + 1}/{len(stratum_b_specs)} done")

    stratum_c: list[ScenarioResult] = []
    for i, (name, start, distance_mi, count) in enumerate(stratum_c_specs):
        stratum_c.append(_run_water_coverage_scenario(name, start, distance_mi, count))
        print(f"  C: {i + 1}/{len(stratum_c_specs)} done")

    report_text = build_report(stratum_a, stratum_b, stratum_c)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
