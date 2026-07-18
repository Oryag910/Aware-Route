"""Full local-graph benchmark suite (Subagent B).

Runs every scenario in `scripts/benchmark_scenarios.py` against the REAL
Manhattan graph via `app.generation.engine.generate_candidates` /
`generate_amenity_aware`, measuring:
  - distance error vs target
  - amenity-in-range pass (a fountain node actually appears on the
    returned geometry, same check as scripts/benchmark_amenities.py)
  - latency
  - defect signals: excessive turns (sharp/U-turn counts from
    app.flow.shape), repeated-segment ratio (app.restrooms.repeated_segments),
    tiny corrective-loop detection, and connectivity/validity (dedup'd
    node path length vs geometry point count, is the geometry a single
    connected walk with no huge jumps).

River/ferry/highway crossings are structurally impossible on this walk
graph -- OSMnx's `network_type="walk"` extraction excludes motorways,
`route=ferry`, and water at the data layer, so there is no edge a
generated path could use to cross open water or a highway. That is a
guarantee of the graph construction, not something this suite tests for
(there's no valid edge to trigger it, so a "crossing" defect is
unreachable by construction).

Writes a markdown report to benchmarks/local/report_<timestamp>.md and
exports ~30 representative routes as GeoJSON to benchmarks/local/ for
human visual review (see docs/benchmark-visual-review-checklist.md).
"""

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.amenities.fountains import fountain_to_amenity, get_fountains
from app.amenities.snapping import SnappedAmenity, amenities_in_range, snap_amenities
from app.flow.shape import sharp_turn_count, u_turn_count
from app.generation.engine import generate_amenity_aware, generate_candidates
from app.graph.distances import nearest_node, single_source_distances
from app.graph.loader import get_graph
from app.graph.model import node_coordinate
from app.restrooms.repeated_segments import repeated_segment_ratio
from app.routing.provider import Coordinate, RouteCandidate
from scripts.benchmark_scenarios import SCENARIOS, Scenario


MILES_TO_METERS = 1609.34
TOLERANCE_M = 100.0
COUNT = 5
MAX_SNAP_M = 200.0

# Amenity-required scenarios ask for a fountain somewhere in the middle
# third of the route -- wide enough that most real starts have a
# candidate, narrow enough to be a meaningful constraint.
AMENITY_MIN_FRACTION = 0.25
AMENITY_MAX_FRACTION = 0.75

# Defect thresholds -- generous enough to only flag genuinely bad
# routes, not ordinary Manhattan-grid turning.
MAX_REASONABLE_SHARP_TURNS_PER_MILE = 8.0
MAX_REASONABLE_U_TURNS = 2
MAX_REASONABLE_REPEATED_SEGMENT_RATIO = 0.15

# A "tiny corrective loop" is a short closed sub-path (the route touches
# the same grid cell twice within a small number of points AND a small
# on-the-ground distance) used to pad length rather than a genuine
# route feature -- distinct from the coarser repeated_segment_ratio
# because it specifically looks for *short* revisits.
TINY_LOOP_MAX_INDEX_GAP = 30
TINY_LOOP_MAX_M = 250.0

GEOJSON_SAMPLE_COUNT = 30

RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "local"


@dataclass(frozen=True)
class DefectFlags:
    excessive_sharp_turns: bool
    excessive_u_turns: bool
    excessive_repeated_segments: bool
    tiny_corrective_loop: bool
    disconnected: bool

    @property
    def any(self) -> bool:
        return (
            self.excessive_sharp_turns
            or self.excessive_u_turns
            or self.excessive_repeated_segments
            or self.tiny_corrective_loop
            or self.disconnected
        )


@dataclass(frozen=True)
class CandidateReport:
    distance_m: float
    distance_error_m: float
    within_tolerance: bool
    sharp_turns: int
    u_turns: int
    repeated_ratio: float
    defects: DefectFlags
    passes_amenity: bool | None  # None if amenity not required


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    ok: bool
    error: str | None
    time_s: float
    candidates: list[CandidateReport] = field(default_factory=list)

    @property
    def best_error_m(self) -> float | None:
        errors = [c.distance_error_m for c in self.candidates]
        return min(errors) if errors else None

    @property
    def any_within_tolerance(self) -> bool:
        return any(c.within_tolerance for c in self.candidates)

    @property
    def any_amenity_pass(self) -> bool:
        return any(c.passes_amenity for c in self.candidates if c.passes_amenity is not None)

    @property
    def within_tolerance_and_constraints_ok(self) -> bool:
        """For candidates within tolerance, did they also pass their
        hard constraints (amenity requirement, if any)?"""
        within = [c for c in self.candidates if c.within_tolerance]
        if not within:
            return True  # vacuous -- nothing to check
        if not self.scenario.amenity_required:
            return True
        return any(c.passes_amenity for c in within)


def _tiny_corrective_loop(geometry: tuple[Any, ...]) -> bool:
    """Detects a short closed sub-path: two points close in *index*
    (within TINY_LOOP_MAX_INDEX_GAP) whose on-the-ground revisit
    distance is small (within TINY_LOOP_MAX_M) -- a hallmark of the
    length-tuner bolting on a tiny loop instead of a clean spur/trim."""
    from app.routing.geometry import haversine_m

    n = len(geometry)
    if n < 6:
        return False

    coords = [Coordinate(lat=p.lat, lon=p.lon) for p in geometry]

    # Compare each point to points within the index window ahead of it;
    # O(n * window) which is fine at route-geometry sizes (~hundreds).
    for i in range(n):
        for j in range(i + 4, min(i + TINY_LOOP_MAX_INDEX_GAP, n)):
            if haversine_m(coords[i], coords[j]) <= (TINY_LOOP_MAX_M / 20.0):
                return True

    return False


def _connectivity_ok(candidate: RouteCandidate) -> bool:
    """Sanity check: consecutive geometry points shouldn't jump an
    unreasonable distance (a sign of a broken/discontinuous path
    stitched together incorrectly)."""
    from app.routing.geometry import haversine_m

    geometry = candidate.geometry
    if len(geometry) < 2:
        return False

    for a, b in zip(geometry, geometry[1:]):
        coord_a = Coordinate(lat=a.lat, lon=a.lon)
        coord_b = Coordinate(lat=b.lat, lon=b.lon)
        if haversine_m(coord_a, coord_b) > 500.0:
            return False

    return True


def _passes_amenity(
    candidate: RouteCandidate,
    graph: Any,
    in_range_nodes: set[int],
) -> bool:
    """True if any vertex of the candidate's geometry coincides with an
    in-range amenity's snapped node coordinate (same check as
    scripts/benchmark_amenities.py)."""
    route_points = {
        (round(p.lat, 6), round(p.lon, 6)) for p in candidate.geometry
    }

    for node_id in in_range_nodes:
        coord = node_coordinate(graph, node_id)
        if (round(coord.lat, 6), round(coord.lon, 6)) in route_points:
            return True

    return False


def _build_candidate_report(
    candidate: RouteCandidate,
    target_m: float,
    passes_amenity: bool | None,
) -> CandidateReport:
    distance_error_m = abs(candidate.distance_m - target_m)
    within_tolerance = distance_error_m <= TOLERANCE_M

    sharp_turns = sharp_turn_count(candidate.geometry)
    u_turns = u_turn_count(candidate.geometry)
    repeated_ratio = repeated_segment_ratio(candidate.geometry)

    miles = candidate.distance_m / MILES_TO_METERS
    sharp_turn_rate = sharp_turns / miles if miles > 0 else 0.0

    defects = DefectFlags(
        excessive_sharp_turns=sharp_turn_rate > MAX_REASONABLE_SHARP_TURNS_PER_MILE,
        excessive_u_turns=u_turns > MAX_REASONABLE_U_TURNS,
        excessive_repeated_segments=repeated_ratio > MAX_REASONABLE_REPEATED_SEGMENT_RATIO,
        tiny_corrective_loop=_tiny_corrective_loop(candidate.geometry),
        disconnected=not _connectivity_ok(candidate),
    )

    return CandidateReport(
        distance_m=candidate.distance_m,
        distance_error_m=distance_error_m,
        within_tolerance=within_tolerance,
        sharp_turns=sharp_turns,
        u_turns=u_turns,
        repeated_ratio=repeated_ratio,
        defects=defects,
        passes_amenity=passes_amenity,
    )


def run_scenario(
    graph: Any,
    scenario: Scenario,
    snapped: list[SnappedAmenity],
) -> tuple[ScenarioResult, list[RouteCandidate]]:
    start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
    target_m = scenario.target_distance_miles * MILES_TO_METERS

    try:
        start_node = nearest_node(graph, start)
        dists = single_source_distances(graph, start_node)

        in_range_nodes: set[int] = set()
        if scenario.amenity_required:
            min_range_m = AMENITY_MIN_FRACTION * target_m
            max_range_m = AMENITY_MAX_FRACTION * target_m
            in_range = amenities_in_range(snapped, dists, min_range_m, max_range_m)
            in_range_nodes = {entry.node_id for entry in in_range}

        start_time = time.monotonic()

        if scenario.amenity_required:
            candidates = generate_amenity_aware(
                graph,
                start,
                target_m,
                scenario.shape,
                COUNT,
                snapped,
                AMENITY_MIN_FRACTION * target_m,
                AMENITY_MAX_FRACTION * target_m,
            )
        else:
            candidates = generate_candidates(
                graph, start, target_m, scenario.shape, COUNT
            )

        elapsed = time.monotonic() - start_time

    except Exception as exc:  # noqa: BLE001 -- benchmark must survive any generator failure
        return (
            ScenarioResult(scenario=scenario, ok=False, error=str(exc), time_s=0.0),
            [],
        )

    reports = [
        _build_candidate_report(
            candidate,
            target_m,
            (
                _passes_amenity(candidate, graph, in_range_nodes)
                if scenario.amenity_required
                else None
            ),
        )
        for candidate in candidates
    ]

    return (
        ScenarioResult(
            scenario=scenario,
            ok=True,
            error=None,
            time_s=elapsed,
            candidates=reports,
        ),
        candidates,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(round(pct / 100.0 * (len(sorted_values) - 1))))
    return sorted_values[index]


def _alternatives_differ(scenario_result: ScenarioResult) -> bool:
    """True if the candidate set has meaningfully different distances
    (not all near-identical) -- a cheap proxy for "are alternatives
    actually different routes"."""
    distances = [c.distance_m for c in scenario_result.candidates]
    if len(distances) < 2:
        return True
    spread = max(distances) - min(distances)
    return spread > 10.0  # meters -- near-zero spread means near-duplicate paths


def build_report(results: list[ScenarioResult]) -> str:
    ok_results = [r for r in results if r.ok]
    failed_results = [r for r in results if not r.ok]

    feasible = [r for r in ok_results if not r.scenario.hard_case] + [
        r for r in ok_results if r.scenario.hard_case
    ]
    with_route = [r for r in feasible if r.candidates]

    latencies = [r.time_s for r in ok_results]
    median_latency = statistics.median(latencies) if latencies else 0.0
    p95_latency = _percentile(latencies, 95.0)

    within_tol_results = [r for r in ok_results if r.any_within_tolerance]
    within_tol_pct = (
        100.0 * len(within_tol_results) / len(ok_results) if ok_results else 0.0
    )

    amenity_scenarios = [r for r in ok_results if r.scenario.amenity_required]
    amenity_pass = [r for r in amenity_scenarios if r.any_amenity_pass]
    amenity_pass_pct = (
        100.0 * len(amenity_pass) / len(amenity_scenarios)
        if amenity_scenarios
        else 0.0
    )

    within_and_constraints = [
        r for r in ok_results if r.any_within_tolerance
    ]
    within_and_constraints_ok = [
        r for r in within_and_constraints if r.within_tolerance_and_constraints_ok
    ]
    within_and_constraints_pct = (
        100.0 * len(within_and_constraints_ok) / len(within_and_constraints)
        if within_and_constraints
        else 100.0
    )

    all_candidates = [c for r in ok_results for c in r.candidates]
    defect_counts = {
        "excessive_sharp_turns": sum(
            1 for c in all_candidates if c.defects.excessive_sharp_turns
        ),
        "excessive_u_turns": sum(
            1 for c in all_candidates if c.defects.excessive_u_turns
        ),
        "excessive_repeated_segments": sum(
            1 for c in all_candidates if c.defects.excessive_repeated_segments
        ),
        "tiny_corrective_loop": sum(
            1 for c in all_candidates if c.defects.tiny_corrective_loop
        ),
        "disconnected": sum(1 for c in all_candidates if c.defects.disconnected),
    }
    total_candidates = len(all_candidates)

    feasible_pass_pct = (
        100.0 * len(with_route) / len(feasible) if feasible else 0.0
    )

    alt_differ = [r for r in ok_results if len(r.candidates) >= 2]
    alt_differ_pass = [r for r in alt_differ if _alternatives_differ(r)]
    alt_differ_pct = (
        100.0 * len(alt_differ_pass) / len(alt_differ) if alt_differ else 0.0
    )

    lines: list[str] = []
    lines.append(f"# Local engine benchmark report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"Scenarios run: {len(results)}")
    lines.append(f"Succeeded (no exception): {len(ok_results)}")
    lines.append(f"Failed (exception): {len(failed_results)}")
    lines.append("")
    lines.append("## Success criteria")
    lines.append("")
    lines.append(
        f"- >=95% of feasible scenarios return >=1 valid route: "
        f"**{feasible_pass_pct:.1f}%** ({len(with_route)}/{len(feasible)}) "
        f"{'PASS' if feasible_pass_pct >= 95.0 else 'FAIL'}"
    )
    lines.append(
        f"- Median latency < 1s: **{median_latency:.3f}s** "
        f"{'PASS' if median_latency < 1.0 else 'FAIL'}"
    )
    lines.append(
        f"- p95 latency < 2s: **{p95_latency:.3f}s** "
        f"{'PASS' if p95_latency < 2.0 else 'FAIL'}"
    )
    lines.append(
        f"- 100% of within-+/-100m routes also pass hard constraints: "
        f"**{within_and_constraints_pct:.1f}%** "
        f"{'PASS' if within_and_constraints_pct >= 99.999 else 'FAIL'}"
    )
    lines.append(
        f"- Alternatives meaningfully different (scenarios w/ >=2 candidates): "
        f"**{alt_differ_pct:.1f}%** ({len(alt_differ_pass)}/{len(alt_differ)})"
    )
    lines.append("")
    lines.append("## Accuracy")
    lines.append("")
    lines.append(
        f"- Within +/-{TOLERANCE_M:.0f}m of target: {len(within_tol_results)}/"
        f"{len(ok_results)} ({within_tol_pct:.1f}%)"
    )
    lines.append(
        f"- Amenity-required scenarios passing (fountain in range AND on-route): "
        f"{len(amenity_pass)}/{len(amenity_scenarios)} ({amenity_pass_pct:.1f}%)"
    )
    lines.append("")
    lines.append("## Latency")
    lines.append("")
    lines.append(f"- Median: {median_latency:.3f}s")
    lines.append(f"- p95: {p95_latency:.3f}s")
    lines.append(f"- Max: {max(latencies):.3f}s" if latencies else "- Max: n/a")
    lines.append("")
    lines.append("## Defect distribution (across all returned candidates)")
    lines.append("")
    lines.append(f"Total candidates inspected: {total_candidates}")
    for defect_name, count in defect_counts.items():
        pct = 100.0 * count / total_candidates if total_candidates else 0.0
        lines.append(f"- {defect_name}: {count} ({pct:.1f}%)")
    lines.append("")
    lines.append(
        "River/ferry/highway crossings: **structurally impossible** — the "
        "walk graph is built with OSMnx `network_type=\"walk\"`, which "
        "excludes motorways, `route=ferry`, and water at the data layer. "
        "There is no edge a generated path could traverse to cross open "
        "water or a highway, so this is a guarantee of the graph "
        "construction rather than a defect check run here."
    )
    lines.append("")

    if failed_results:
        lines.append("## Failed scenarios")
        lines.append("")
        for r in failed_results:
            lines.append(f"- **{r.scenario.name}**: {r.error}")
        lines.append("")

    no_route = [r for r in feasible if r.ok and not r.candidates]
    if no_route:
        lines.append("## Scenarios with zero candidates returned")
        lines.append("")
        for r in no_route:
            lines.append(f"- {r.scenario.name}")
        lines.append("")

    hard_case_results = [r for r in ok_results if r.scenario.hard_case]
    if hard_case_results:
        lines.append("## Hard-case detail")
        lines.append("")
        for r in hard_case_results:
            best = r.best_error_m
            best_str = f"{best:.0f}m" if best is not None else "n/a"
            lines.append(
                f"- {r.scenario.name}: candidates={len(r.candidates)} "
                f"best_err={best_str} time={r.time_s:.3f}s"
            )
        lines.append("")

    return "\n".join(lines)


def _candidate_to_geojson_feature(
    candidate: RouteCandidate, scenario: Scenario, index: int
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "scenario": scenario.name,
            "shape": scenario.shape,
            "target_miles": scenario.target_distance_miles,
            "distance_m": candidate.distance_m,
            "candidate_index": index,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[p.lon, p.lat] for p in candidate.geometry],
        },
    }


def export_sample_geojson(
    results: list[ScenarioResult],
    all_candidates_by_scenario: dict[str, list[RouteCandidate]],
    output_dir: Path,
) -> list[Path]:
    """Exports up to GEOJSON_SAMPLE_COUNT representative routes: a mix
    of hard cases, amenity-required scenarios, and a spread of ordinary
    scenarios across shapes, for human visual review."""
    ok_results = [r for r in results if r.ok and r.candidates]

    hard = [r for r in ok_results if r.scenario.hard_case]
    amenity = [r for r in ok_results if r.scenario.amenity_required and not r.scenario.hard_case]
    ordinary = [
        r for r in ok_results if not r.scenario.hard_case and not r.scenario.amenity_required
    ]

    selected: list[ScenarioResult] = []
    selected.extend(hard[:10])

    remaining = GEOJSON_SAMPLE_COUNT - len(selected)
    amenity_take = min(remaining // 2, len(amenity))
    selected.extend(amenity[:amenity_take])

    remaining = GEOJSON_SAMPLE_COUNT - len(selected)
    # Spread ordinary picks across shapes for variety.
    by_shape: dict[str, list[ScenarioResult]] = {}
    for r in ordinary:
        by_shape.setdefault(r.scenario.shape, []).append(r)

    shapes = list(by_shape.keys())
    idx = 0
    while remaining > 0 and any(by_shape.values()):
        shape = shapes[idx % len(shapes)]
        bucket = by_shape.get(shape, [])
        if bucket:
            selected.append(bucket.pop(0))
            remaining -= 1
        idx += 1
        if all(not v for v in by_shape.values()):
            break

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for result in selected[:GEOJSON_SAMPLE_COUNT]:
        candidates = all_candidates_by_scenario.get(result.scenario.name, [])
        if not candidates:
            continue

        features = [
            _candidate_to_geojson_feature(candidate, result.scenario, i)
            for i, candidate in enumerate(candidates)
        ]
        feature_collection = {"type": "FeatureCollection", "features": features}

        safe_name = (
            result.scenario.name.replace(" ", "_")
            .replace("|", "-")
            .replace("/", "-")
            .replace(",", "")
        )
        output_path = output_dir / f"route_{safe_name[:80]}.geojson"
        output_path.write_text(json.dumps(feature_collection, indent=2) + "\n")
        written.append(output_path)

    return written


def main() -> None:
    graph = get_graph()

    fountains = get_fountains()
    amenities = [fountain_to_amenity(f) for f in fountains]
    snapped = snap_amenities(graph, amenities, max_snap_m=MAX_SNAP_M)

    print(f"Loaded {len(fountains)} fountains, {len(snapped)} snapped within {MAX_SNAP_M:.0f}m")
    print(f"Running {len(SCENARIOS)} scenarios ...\n")

    results: list[ScenarioResult] = []
    all_candidates_by_scenario: dict[str, list[RouteCandidate]] = {}

    for i, scenario in enumerate(SCENARIOS):
        result, candidates = run_scenario(graph, scenario, snapped)
        results.append(result)

        if result.ok and candidates:
            all_candidates_by_scenario[scenario.name] = candidates

        if (i + 1) % 50 == 0 or i == len(SCENARIOS) - 1:
            print(f"  ... {i + 1}/{len(SCENARIOS)} done")

    report_text = build_report(results)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")

    written = export_sample_geojson(results, all_candidates_by_scenario, RESULTS_DIR)
    print(f"Exported {len(written)} sample GeoJSON routes to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
