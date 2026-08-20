"""V1 vs V2 amenity-aware round-route generator comparison (PR #16).

Scope, explicitly: `shape == "round"` scenarios with
`amenity_required == True` from `scripts/benchmark_scenarios.SCENARIOS`
(discovered at import time, not assumed -- see `SCOPE_SCENARIOS`). This
is the only subset where V1 and V2 are doing the same job:

  - V1 = `app.generation.amenity_first.through_amenities_pairs`
    (called directly, NOT via `engine.generate_amenity_aware` -- since
    PR #16 that wrapper routes through `generate_routes`'s
    `ROUND_GENERATOR`-gated "round" branch, which defaults to the
    polygon generator; going through it here would make "V1" silently
    run V2 too), the original turnaround-at-the-amenity generator
    (`amenity_first.py`, which uses `length_tune.py`'s spur-based
    tuner and can produce a materially out-and-back "round" route --
    see its `MAX_AMENITY_ROUND_EDGE_REUSE_RATIO` gate).
  - V2 = `app.generation.engine.generate_polygon_loop_amenity_candidates()`,
    PR #16's multi-anchor polygon loop routed THROUGH the amenity as a
    waypoint on one leg (`polygon_amenity.py`), never as the
    turnaround.

`mix`, `out_and_back`, and every non-amenity scenario are OUT OF SCOPE
and NOT run here.

OFFLINE DATASET CAVEAT (read before trusting the amenity-in-range
numbers below): this benchmark validates offline amenity-placement
MECHANICS against the bundled fountain dataset only (no restroom kind
exists in `app.amenities.fountains`). It does NOT validate live
Supabase restroom availability or the `/routes/with-restroom`
restroom-match contract -- those are covered by
`tests/test_routes_with_restroom.py` and
`tests/generation/test_local_scoring.py`. Restroom-vs-fountain
priority logic is exercised there and in
`tests/generation/test_polygon_amenity.py`'s synthetic-graph tests,
not by this real-Manhattan run.

CRITICAL measurement note (the actual point of this PR): every
distance/range number below is measured at the amenity's ACTUAL
CUMULATIVE ALONG-ROUTE POSITION on the candidate's real rendered
geometry (`app.amenities.matching.match_amenities_to_route`, the same
helper `local_scoring.py` uses), never by shortest graph distance from
start. That distinction is exactly what this PR replaces.
"""

import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.amenities.fountains import fountain_to_amenity, get_fountains
from app.amenities.matching import match_amenities_to_route
from app.amenities.models import Amenity
from app.amenities.snapping import SnappedAmenity, snap_amenities
from app.generation.amenity_first import through_amenities_pairs
from app.generation.engine import generate_polygon_loop_amenity_candidates
from app.graph.loader import get_graph
from app.restrooms.scoring import mile_range_error_m
from app.routing.provider import Coordinate, RouteCandidate
from scripts.benchmark_scenarios import SCENARIOS, Scenario
from scripts.benchmark_suite import (
    AMENITY_MAX_FRACTION,
    AMENITY_MIN_FRACTION,
    COUNT,
    DISTINCT_ALTERNATIVE_MAX_OVERLAP,
    MAX_SNAP_M,
    MILES_TO_METERS,
    TOLERANCE_M,
    CandidateReport,
    _build_candidate_report,
    _pairwise_segment_overlaps,
    _percentile,
    _route_segment_signature,
)


RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "local"

Generator = Literal["v1", "v2"]

SCOPE_SCENARIOS: tuple[Scenario, ...] = tuple(
    s for s in SCENARIOS if s.shape == "round" and s.amenity_required
)


@dataclass(frozen=True)
class AmenityCandidateReport:
    base: CandidateReport
    amenity_on_route: bool
    amenity_in_range: bool
    mile_marker_m: float | None
    mile_range_error_m: float | None


def _build_amenity_report(
    candidate: RouteCandidate,
    target_m: float,
    amenities: list[Amenity],
    min_range_m: float,
    max_range_m: float,
) -> AmenityCandidateReport:
    base = _build_candidate_report(candidate, target_m, None)

    matches = match_amenities_to_route(candidate.geometry, amenities)
    if not matches:
        return AmenityCandidateReport(
            base=base, amenity_on_route=False, amenity_in_range=False,
            mile_marker_m=None, mile_range_error_m=None,
        )

    best = min(
        matches,
        key=lambda match: mile_range_error_m(match.mile_marker_m, min_range_m, max_range_m),
    )
    error = mile_range_error_m(best.mile_marker_m, min_range_m, max_range_m)

    return AmenityCandidateReport(
        base=base,
        amenity_on_route=True,
        amenity_in_range=error <= 0.0,
        mile_marker_m=best.mile_marker_m,
        mile_range_error_m=error,
    )


@dataclass(frozen=True)
class GeneratorRun:
    ok: bool
    error: str | None
    time_s: float
    candidates: list[AmenityCandidateReport] = field(default_factory=list)
    segment_overlaps: tuple[float, ...] = ()
    exact_duplicate_count: int = 0

    @property
    def any_within_tolerance(self) -> bool:
        return any(c.base.within_tolerance for c in self.candidates)

    @property
    def any_in_range(self) -> bool:
        return any(c.amenity_in_range for c in self.candidates)

    @property
    def any_within_tolerance_and_in_range(self) -> bool:
        return any(c.base.within_tolerance and c.amenity_in_range for c in self.candidates)


def _run_generator(
    graph: object,
    start: Coordinate,
    target_m: float,
    snapped: list[SnappedAmenity],
    amenities: list[Amenity],
    min_range_m: float,
    max_range_m: float,
    generator: Generator,
) -> GeneratorRun:
    try:
        start_time = time.monotonic()
        if generator == "v1":
            # Call V1's generator directly rather than through
            # engine.generate_amenity_aware -- that wrapper now routes
            # through generate_routes's `ROUND_GENERATOR`-gated "round"
            # branch (PR #16), which defaults to the polygon
            # generator. Going through it here would make the "V1"
            # arm of this comparison silently run V2 too whenever the
            # environment doesn't override the flag, comparing V2
            # against itself.
            triples = through_amenities_pairs(
                graph, start, target_m, snapped, min_range_m, max_range_m,
                "round", COUNT,
            )
            candidates: list[RouteCandidate] = [candidate for candidate, _node_path, _shape in triples]
        else:
            candidates = generate_polygon_loop_amenity_candidates(
                graph, start, target_m, COUNT, snapped, min_range_m, max_range_m
            )
        elapsed = time.monotonic() - start_time
    except Exception as exc:  # noqa: BLE001 -- benchmark must survive generator failures
        return GeneratorRun(ok=False, error=str(exc), time_s=0.0)

    reports = [
        _build_amenity_report(c, target_m, amenities, min_range_m, max_range_m)
        for c in candidates
    ]
    signatures = [_route_segment_signature(c.geometry) for c in candidates]
    exact_duplicates = sum(
        1
        for i in range(len(signatures))
        for j in range(i + 1, len(signatures))
        if signatures[i] == signatures[j]
    )

    return GeneratorRun(
        ok=True,
        error=None,
        time_s=elapsed,
        candidates=reports,
        segment_overlaps=_pairwise_segment_overlaps(candidates),
        exact_duplicate_count=exact_duplicates,
    )


@dataclass(frozen=True)
class ScenarioComparison:
    scenario: Scenario
    v1: GeneratorRun
    v2: GeneratorRun


def run_scenario(
    graph: object,
    scenario: Scenario,
    snapped: list[SnappedAmenity],
    amenities: list[Amenity],
) -> ScenarioComparison:
    start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
    target_m = scenario.target_distance_miles * MILES_TO_METERS
    min_range_m = AMENITY_MIN_FRACTION * target_m
    max_range_m = AMENITY_MAX_FRACTION * target_m

    return ScenarioComparison(
        scenario=scenario,
        v1=_run_generator(graph, start, target_m, snapped, amenities, min_range_m, max_range_m, "v1"),
        v2=_run_generator(graph, start, target_m, snapped, amenities, min_range_m, max_range_m, "v2"),
    )


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: list[float]) -> float | None:
    return _percentile(values, 95.0) if values else None


def _fmt(value: float | None, spec: str = "{:.3f}") -> str:
    return spec.format(value) if value is not None else "n/a"


@dataclass(frozen=True)
class ReliabilitySummary:
    scenarios_run: int
    scenario_success_pct: float  # ran without exception
    any_candidate_pct: float
    amenity_on_route_rate: float  # % of candidates
    amenity_in_range_rate: float  # % of candidates
    distance_ok_pct: float  # scenarios with >=1 candidate within tolerance
    distance_and_range_ok_pct: float  # scenarios with >=1 candidate satisfying BOTH


def _summarize_reliability(runs: list[GeneratorRun]) -> ReliabilitySummary:
    ok_runs = [r for r in runs if r.ok]
    with_candidates = [r for r in ok_runs if r.candidates]
    all_candidates = [c for r in ok_runs for c in r.candidates]
    n = len(all_candidates)

    return ReliabilitySummary(
        scenarios_run=len(runs),
        scenario_success_pct=100.0 * len(ok_runs) / len(runs) if runs else 0.0,
        any_candidate_pct=100.0 * len(with_candidates) / len(ok_runs) if ok_runs else 0.0,
        amenity_on_route_rate=(
            100.0 * sum(1 for c in all_candidates if c.amenity_on_route) / n if n else 0.0
        ),
        amenity_in_range_rate=(
            100.0 * sum(1 for c in all_candidates if c.amenity_in_range) / n if n else 0.0
        ),
        distance_ok_pct=(
            100.0 * sum(1 for r in ok_runs if r.any_within_tolerance) / len(ok_runs)
            if ok_runs else 0.0
        ),
        distance_and_range_ok_pct=(
            100.0 * sum(1 for r in ok_runs if r.any_within_tolerance_and_in_range) / len(ok_runs)
            if ok_runs else 0.0
        ),
    )


@dataclass(frozen=True)
class DiversitySummary:
    scenarios_with_alternatives: int
    exact_duplicate_pairs: int
    median_pairwise_overlap: float | None
    pct_pairs_distinct: float | None
    pct_scenarios_all_distinct: float | None


def _summarize_diversity(runs: list[GeneratorRun]) -> DiversitySummary:
    alt_runs = [r for r in runs if r.ok and len(r.candidates) >= 2]
    exact_dupes = sum(r.exact_duplicate_count for r in alt_runs)
    all_overlaps = [o for r in alt_runs for o in r.segment_overlaps]
    all_distinct_scenarios = [
        r for r in alt_runs
        if all(o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP for o in r.segment_overlaps)
    ]

    return DiversitySummary(
        scenarios_with_alternatives=len(alt_runs),
        exact_duplicate_pairs=exact_dupes,
        median_pairwise_overlap=statistics.median(all_overlaps) if all_overlaps else None,
        pct_pairs_distinct=(
            100.0 * sum(1 for o in all_overlaps if o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP)
            / len(all_overlaps)
            if all_overlaps else None
        ),
        pct_scenarios_all_distinct=(
            100.0 * len(all_distinct_scenarios) / len(alt_runs) if alt_runs else None
        ),
    )


_GEOMETRY_METRICS: tuple[tuple[str, str, str], ...] = (
    ("radial_exposure", "radial exposure", "{:.3f}"),
    ("elongation_ratio", "elongation", "{:.2f}"),
    ("isoperimetric_quotient", "compactness", "{:.3f}"),
)


def _pooled_values(runs: list[GeneratorRun], attr: str) -> list[float]:
    return [getattr(c.base, attr) for r in runs if r.ok for c in r.candidates]


def _top_ranked_values(runs: list[GeneratorRun], attr: str) -> list[float]:
    return [getattr(r.candidates[0].base, attr) for r in runs if r.ok and r.candidates]


def _defect_rate(runs: list[GeneratorRun], attr: str) -> float:
    candidates = [c for r in runs if r.ok for c in r.candidates]
    if not candidates:
        return 0.0
    return 100.0 * sum(1 for c in candidates if getattr(c.base.defects, attr)) / len(candidates)


def build_report(comparisons: list[ScenarioComparison]) -> str:
    v1_runs = [c.v1 for c in comparisons]
    v2_runs = [c.v2 for c in comparisons]

    v1_reliability = _summarize_reliability(v1_runs)
    v2_reliability = _summarize_reliability(v2_runs)
    v1_diversity = _summarize_diversity(v1_runs)
    v2_diversity = _summarize_diversity(v2_runs)

    v1_latencies = [r.time_s for r in v1_runs if r.ok]
    v2_latencies = [r.time_s for r in v2_runs if r.ok]

    v1_range_errors = [
        c.mile_range_error_m for r in v1_runs if r.ok for c in r.candidates
        if c.amenity_on_route and c.mile_range_error_m is not None
    ]
    v2_range_errors = [
        c.mile_range_error_m for r in v2_runs if r.ok for c in r.candidates
        if c.amenity_on_route and c.mile_range_error_m is not None
    ]

    v1_failed = [c for c in comparisons if not c.v1.ok]
    v2_failed = [c for c in comparisons if not c.v2.ok]

    lines: list[str] = []
    lines.append(
        f"# Polygon-loop amenity-aware V1 vs V2 comparison (PR #16) — "
        f"{datetime.now().isoformat(timespec='seconds')}"
    )
    lines.append("")
    lines.append(
        f"**Scope: `shape == \"round\"`, `amenity_required == True` "
        f"scenarios only -- {len(SCOPE_SCENARIOS)} of the full "
        f"537-scenario suite (includes "
        f"{sum(1 for s in SCOPE_SCENARIOS if s.hard_case)} hard-case "
        f"scenarios).** `mix`/`out_and_back`/non-amenity scenarios were "
        f"NOT run through this comparison."
    )
    lines.append("")
    lines.append(
        "**Offline dataset caveat**: validates amenity-placement "
        "MECHANICS against the bundled fountain dataset only (no "
        "restroom kind exists offline) -- does NOT validate live "
        "Supabase restroom availability or the `/routes/with-restroom` "
        "contract; see `tests/test_routes_with_restroom.py` and "
        "`tests/generation/test_polygon_amenity.py` for that."
    )
    lines.append("")
    lines.append(
        "**Measurement note**: every amenity-position number below is "
        "the amenity's ACTUAL CUMULATIVE ALONG-ROUTE POSITION on the "
        "candidate's real rendered geometry "
        "(`app.amenities.matching.match_amenities_to_route`, the same "
        "helper `local_scoring.py` uses) -- never shortest graph "
        "distance from start."
    )
    lines.append("")
    lines.append(
        "V1 = `amenity_first.through_amenities_pairs` called directly "
        "(amenity-as-turnaround; bypasses the `ROUND_GENERATOR` flag "
        "on purpose -- see module docstring). V2 = "
        "`engine.generate_polygon_loop_amenity_candidates()` (PR #16 "
        "amenity-as-waypoint)."
    )
    lines.append("")

    lines.append("## Reliability")
    lines.append("")
    lines.append("| metric | V1 | V2 |")
    lines.append("|---|---|---|")
    lines.append(f"| scenarios run | {v1_reliability.scenarios_run} | {v2_reliability.scenarios_run} |")
    lines.append(
        f"| scenario success (no exception) | "
        f"{v1_reliability.scenario_success_pct:.1f}% | "
        f"{v2_reliability.scenario_success_pct:.1f}% |"
    )
    lines.append(
        f"| scenarios with >=1 candidate | {v1_reliability.any_candidate_pct:.1f}% | "
        f"{v2_reliability.any_candidate_pct:.1f}% |"
    )
    lines.append(
        f"| amenity-on-route rate (of candidates) | "
        f"{v1_reliability.amenity_on_route_rate:.1f}% | "
        f"{v2_reliability.amenity_on_route_rate:.1f}% |"
    )
    lines.append(
        f"| amenity-in-range rate (of candidates) | "
        f"{v1_reliability.amenity_in_range_rate:.1f}% | "
        f"{v2_reliability.amenity_in_range_rate:.1f}% |"
    )
    lines.append(
        f"| scenarios with >=1 candidate within +/-{TOLERANCE_M:.0f}m | "
        f"{v1_reliability.distance_ok_pct:.1f}% | {v2_reliability.distance_ok_pct:.1f}% |"
    )
    lines.append(
        f"| scenarios with >=1 candidate BOTH within tolerance AND "
        f"amenity-in-range | {v1_reliability.distance_and_range_ok_pct:.1f}% | "
        f"{v2_reliability.distance_and_range_ok_pct:.1f}% |"
    )
    lines.append("")

    lines.append("## Distance")
    lines.append("")
    lines.append(
        "Top-route = the #1-ranked candidate per scenario; "
        "scenario-best = the closest-to-target candidate per scenario."
    )
    lines.append("")
    lines.append("| metric | V1 median | V1 p95 | V2 median | V2 p95 |")
    lines.append("|---|---|---|---|---|")
    v1_top_err = _top_ranked_values(v1_runs, "distance_error_m")
    v2_top_err = _top_ranked_values(v2_runs, "distance_error_m")
    lines.append(
        f"| top-route distance error (m) | {_fmt(_median(v1_top_err), '{:.0f}')} | "
        f"{_fmt(_p95(v1_top_err), '{:.0f}')} | {_fmt(_median(v2_top_err), '{:.0f}')} | "
        f"{_fmt(_p95(v2_top_err), '{:.0f}')} |"
    )
    v1_best_err = [
        min(c.base.distance_error_m for c in r.candidates)
        for r in v1_runs if r.ok and r.candidates
    ]
    v2_best_err = [
        min(c.base.distance_error_m for c in r.candidates)
        for r in v2_runs if r.ok and r.candidates
    ]
    lines.append(
        f"| scenario-best distance error (m) | {_fmt(_median(v1_best_err), '{:.0f}')} | "
        f"{_fmt(_p95(v1_best_err), '{:.0f}')} | {_fmt(_median(v2_best_err), '{:.0f}')} | "
        f"{_fmt(_p95(v2_best_err), '{:.0f}')} |"
    )
    lines.append("")

    lines.append("## Amenity mile-range accuracy")
    lines.append("")
    lines.append(
        "Error is 0 when the amenity's actual cumulative position "
        "lands inside the requested range, else distance (m) to the "
        "nearest bound -- only candidates where the amenity is "
        "actually on the route are included."
    )
    lines.append("")
    lines.append(
        f"- V1 mile-range error: median {_fmt(_median(v1_range_errors), '{:.0f}')}m, "
        f"p95 {_fmt(_p95(v1_range_errors), '{:.0f}')}m "
        f"(n={len(v1_range_errors)})"
    )
    lines.append(
        f"- V2 mile-range error: median {_fmt(_median(v2_range_errors), '{:.0f}')}m, "
        f"p95 {_fmt(_p95(v2_range_errors), '{:.0f}')}m "
        f"(n={len(v2_range_errors)})"
    )
    lines.append("")

    lines.append("## Geometry")
    lines.append("")
    lines.append("### Candidate-pooled")
    lines.append("")
    lines.append("| metric | V1 median | V1 p95 | V2 median | V2 p95 |")
    lines.append("|---|---|---|---|---|")
    for attr, label, spec in _GEOMETRY_METRICS:
        v1v = _pooled_values(v1_runs, attr)
        v2v = _pooled_values(v2_runs, attr)
        lines.append(
            f"| {label} | {_fmt(_median(v1v), spec)} | {_fmt(_p95(v1v), spec)} | "
            f"{_fmt(_median(v2v), spec)} | {_fmt(_p95(v2v), spec)} |"
        )
    lines.append("")
    lines.append("### Top-ranked route per scenario")
    lines.append("")
    lines.append("| metric | V1 median | V1 p95 | V2 median | V2 p95 |")
    lines.append("|---|---|---|---|---|")
    for attr, label, spec in _GEOMETRY_METRICS:
        v1v = _top_ranked_values(v1_runs, attr)
        v2v = _top_ranked_values(v2_runs, attr)
        lines.append(
            f"| {label} | {_fmt(_median(v1v), spec)} | {_fmt(_p95(v1v), spec)} | "
            f"{_fmt(_median(v2v), spec)} | {_fmt(_p95(v2v), spec)} |"
        )
    lines.append("")
    lines.append("### Defect rates (candidate-pooled)")
    lines.append("")
    lines.append("| defect | V1 | V2 |")
    lines.append("|---|---|---|")
    for attr, label in (
        ("excessive_repeated_segments", "excessive repeated segments"),
        ("excessive_u_turns", "excessive U-turns"),
        ("short_start_return_spur", "short start-return spur"),
        ("disconnected", "disconnected"),
    ):
        lines.append(
            f"| {label} | {_defect_rate(v1_runs, attr):.1f}% | "
            f"{_defect_rate(v2_runs, attr):.1f}% |"
        )
    lines.append("")

    lines.append("## Diversity (rendered-segment overlap)")
    lines.append("")
    lines.append("Same methodology as PR #15's comparison.")
    lines.append("")
    lines.append("| metric | V1 | V2 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| scenarios with >=2 candidates | "
        f"{v1_diversity.scenarios_with_alternatives} | "
        f"{v2_diversity.scenarios_with_alternatives} |"
    )
    lines.append(
        f"| exact-duplicate candidate pairs | "
        f"{v1_diversity.exact_duplicate_pairs} | {v2_diversity.exact_duplicate_pairs} |"
    )
    lines.append(
        f"| median pairwise overlap (pooled) | "
        f"{_fmt(v1_diversity.median_pairwise_overlap)} | "
        f"{_fmt(v2_diversity.median_pairwise_overlap)} |"
    )
    lines.append(
        f"| candidate pairs <= {DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f} overlap | "
        f"{_fmt(v1_diversity.pct_pairs_distinct, '{:.1f}%')} | "
        f"{_fmt(v2_diversity.pct_pairs_distinct, '{:.1f}%')} |"
    )
    lines.append(
        f"| scenarios where ALL pairs are distinct | "
        f"{_fmt(v1_diversity.pct_scenarios_all_distinct, '{:.1f}%')} | "
        f"{_fmt(v2_diversity.pct_scenarios_all_distinct, '{:.1f}%')} |"
    )
    lines.append("")

    lines.append("## Latency")
    lines.append("")
    lines.append(
        f"- V1: median {statistics.median(v1_latencies):.3f}s, "
        f"p95 {_percentile(v1_latencies, 95.0):.3f}s, "
        f"max {max(v1_latencies):.3f}s" if v1_latencies else "- V1: n/a"
    )
    lines.append(
        f"- V2: median {statistics.median(v2_latencies):.3f}s, "
        f"p95 {_percentile(v2_latencies, 95.0):.3f}s, "
        f"max {max(v2_latencies):.3f}s" if v2_latencies else "- V2: n/a"
    )
    lines.append("")

    if v1_failed or v2_failed:
        lines.append("## Exceptions")
        lines.append("")
        for c in v1_failed:
            lines.append(f"- V1 raised on **{c.scenario.name}**: {c.v1.error}")
        for c in v2_failed:
            lines.append(f"- V2 raised on **{c.scenario.name}**: {c.v2.error}")
        lines.append("")

    v2_zero = [c for c in comparisons if c.v2.ok and not c.v2.candidates]
    if v2_zero:
        lines.append(f"## V2 scenarios with zero candidates ({len(v2_zero)})")
        lines.append("")
        for c in v2_zero:
            lines.append(f"- {c.scenario.name}")
        lines.append("")

    hard_cases = [c for c in comparisons if c.scenario.hard_case]
    if hard_cases:
        lines.append("## Hard-case detail")
        lines.append("")
        for c in hard_cases:
            v1_n = len(c.v1.candidates) if c.v1.ok else 0
            v2_n = len(c.v2.candidates) if c.v2.ok else 0
            lines.append(
                f"- {c.scenario.name}: V1 candidates={v1_n} "
                f"time={c.v1.time_s:.3f}s | V2 candidates={v2_n} "
                f"time={c.v2.time_s:.3f}s"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    graph = get_graph()

    fountains = get_fountains()
    amenities = [fountain_to_amenity(f) for f in fountains]
    snapped = snap_amenities(graph, amenities, max_snap_m=MAX_SNAP_M)
    print(f"Loaded {len(fountains)} fountains, {len(snapped)} snapped within {MAX_SNAP_M:.0f}m")

    print(
        f"Running {len(SCOPE_SCENARIOS)} round/amenity-required scenarios "
        f"through V1 and V2 ...\n"
    )

    comparisons: list[ScenarioComparison] = []
    for i, scenario in enumerate(SCOPE_SCENARIOS):
        comparisons.append(run_scenario(graph, scenario, snapped, amenities))
        if (i + 1) % 20 == 0 or i == len(SCOPE_SCENARIOS) - 1:
            print(f"  ... {i + 1}/{len(SCOPE_SCENARIOS)} done")

    report_text = build_report(comparisons)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"polygon_amenity_v1_vs_v2_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
