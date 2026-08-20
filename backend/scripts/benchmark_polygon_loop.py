"""V1 vs V2 round-route generator comparison (PR #15).

Scope, explicitly: this compares TWO GENERATORS on the SAME scenario
subset -- `shape == "round"` scenarios with `amenity_required == False`
from `scripts/benchmark_scenarios.SCENARIOS` (91 of the suite's 537,
including the 4 hard-case scenarios that are also round/non-amenity).
This is the only subset where the comparison is actually apples-to-
apples:

  - V1 = `app.generation.engine.generate_candidates(shape="round")`,
    the existing turnaround-based generator (`round_route.py` +
    `length_tune.py`'s spur-based tuner).
  - V2 = `app.generation.engine.generate_polygon_loop_candidates()`,
    PR #15's multi-anchor polygon-loop generator (`polygon_loop.py`).

`mix`, `out_and_back`, and every amenity-required scenario are OUT OF
SCOPE and NOT run here -- `mix`/amenity-aware pools still call V1's
`round_pairs`/`through_amenities_pairs` internally regardless of this
script, so running them through this comparison would misleadingly
label V1-generated routes as "V2". Full-suite reliability numbers
(537 scenarios, all shapes) are PR #14's
`backend/benchmarks/local/report_20260820_122312.md`; this script does
not re-run or overwrite that report.

Per-candidate metrics reuse `scripts.benchmark_suite._build_candidate_report`
(distance accuracy, defect flags, and the PR #14 loop-geometry metrics)
so V1 and V2 are scored by the exact same yardstick. Candidate-diversity
(rendered-segment overlap) reuses `scripts.benchmark_suite`'s pairwise
overlap methodology, same rationale.

This report presents geometry three ways -- see `build_report` section
headers:
  - candidate-pooled: every returned candidate from every scenario,
    pooled together (scenarios with more candidates weigh more).
  - top-ranked route per scenario: just the #1-ranked (roundest-first)
    candidate from each scenario -- the route a user would actually be
    shown first.
  - per-scenario median, summarized across scenarios: each scenario's
    own median across its candidates, then summarized -- every
    scenario weighs equally regardless of candidate count.
"""

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.generation.engine import generate_candidates, generate_polygon_loop_candidates
from app.graph.loader import get_graph
from app.routing.provider import Coordinate, RouteCandidate
from scripts.benchmark_scenarios import SCENARIOS, Scenario
from scripts.benchmark_suite import (
    COUNT,
    DISTINCT_ALTERNATIVE_MAX_OVERLAP,
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
    s for s in SCENARIOS if s.shape == "round" and not s.amenity_required
)


@dataclass(frozen=True)
class GeneratorRun:
    ok: bool
    error: str | None
    time_s: float
    candidates: list[CandidateReport] = field(default_factory=list)
    # Pairwise undirected rendered-segment overlap between every
    # candidate pair this scenario returned (empty if <2 candidates) --
    # same methodology as benchmark_suite._pairwise_segment_overlaps.
    segment_overlaps: tuple[float, ...] = ()
    # Candidate pairs whose rendered-segment signature is IDENTICAL
    # (overlap == 1.0) -- i.e. two different templates/turnarounds
    # collapsed onto the exact same physical route.
    exact_duplicate_count: int = 0

    @property
    def any_within_tolerance(self) -> bool:
        return any(c.within_tolerance for c in self.candidates)


@dataclass(frozen=True)
class ScenarioComparison:
    scenario: Scenario
    v1: GeneratorRun
    v2: GeneratorRun


def _run_generator(
    graph: object, start: Coordinate, target_m: float, generator: Generator
) -> GeneratorRun:
    try:
        start_time = time.monotonic()
        if generator == "v1":
            candidates: list[RouteCandidate] = generate_candidates(
                graph, start, target_m, "round", COUNT
            )
        else:
            candidates = generate_polygon_loop_candidates(
                graph, start, target_m, COUNT
            )
        elapsed = time.monotonic() - start_time
    except Exception as exc:  # noqa: BLE001 -- benchmark must survive generator failures
        return GeneratorRun(ok=False, error=str(exc), time_s=0.0)

    reports = [
        _build_candidate_report(candidate, target_m, None) for candidate in candidates
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


def run_scenario(graph: object, scenario: Scenario) -> ScenarioComparison:
    start = Coordinate(lat=scenario.start_lat, lon=scenario.start_lon)
    target_m = scenario.target_distance_miles * MILES_TO_METERS

    return ScenarioComparison(
        scenario=scenario,
        v1=_run_generator(graph, start, target_m, "v1"),
        v2=_run_generator(graph, start, target_m, "v2"),
    )


@dataclass(frozen=True)
class GeneratorSummary:
    label: str
    scenarios_run: int
    success_pct: float  # >=1 candidate returned (of scenarios that didn't raise)
    within_tolerance_pct: float  # >=1 candidate within +/-TOLERANCE_M
    median_latency_s: float
    p95_latency_s: float
    candidate_count: int
    median_radial_exposure: float | None
    p95_radial_exposure: float | None
    median_elongation_ratio: float | None
    p95_elongation_ratio: float | None
    median_isoperimetric_quotient: float | None
    p95_isoperimetric_quotient: float | None
    excessive_repeated_segments_pct: float
    short_start_return_spur_pct: float
    disconnected_pct: float


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: list[float]) -> float | None:
    return _percentile(values, 95.0) if values else None


def _summarize(label: str, runs: list[GeneratorRun]) -> GeneratorSummary:
    ok_runs = [r for r in runs if r.ok]
    latencies = [r.time_s for r in ok_runs]
    with_candidates = [r for r in ok_runs if r.candidates]
    within_tol = [r for r in ok_runs if r.any_within_tolerance]

    all_candidates = [c for r in ok_runs for c in r.candidates]
    n = len(all_candidates)

    radial = [c.radial_exposure for c in all_candidates]
    elongation = [c.elongation_ratio for c in all_candidates]
    compactness = [c.isoperimetric_quotient for c in all_candidates]

    return GeneratorSummary(
        label=label,
        scenarios_run=len(runs),
        success_pct=100.0 * len(with_candidates) / len(ok_runs) if ok_runs else 0.0,
        within_tolerance_pct=100.0 * len(within_tol) / len(ok_runs) if ok_runs else 0.0,
        median_latency_s=statistics.median(latencies) if latencies else 0.0,
        p95_latency_s=_percentile(latencies, 95.0) if latencies else 0.0,
        candidate_count=n,
        median_radial_exposure=_median(radial),
        p95_radial_exposure=_p95(radial),
        median_elongation_ratio=_median(elongation),
        p95_elongation_ratio=_p95(elongation),
        median_isoperimetric_quotient=_median(compactness),
        p95_isoperimetric_quotient=_p95(compactness),
        excessive_repeated_segments_pct=(
            100.0 * sum(1 for c in all_candidates if c.defects.excessive_repeated_segments) / n
            if n else 0.0
        ),
        short_start_return_spur_pct=(
            100.0 * sum(1 for c in all_candidates if c.defects.short_start_return_spur) / n
            if n else 0.0
        ),
        disconnected_pct=(
            100.0 * sum(1 for c in all_candidates if c.defects.disconnected) / n if n else 0.0
        ),
    )


@dataclass(frozen=True)
class DiversitySummary:
    label: str
    scenarios_with_alternatives: int  # scenarios with >=2 candidates
    exact_duplicate_pairs: int
    median_pairwise_overlap: float | None
    median_of_scenario_medians: float | None
    pct_pairs_distinct: float | None  # of all pooled pairs, overlap <= threshold
    pct_scenarios_with_distinct_alt: float | None  # >=1 distinct pair
    pct_scenarios_all_distinct: float | None  # EVERY pair in the scenario is distinct


def _summarize_diversity(label: str, runs: list[GeneratorRun]) -> DiversitySummary:
    alt_runs = [r for r in runs if r.ok and len(r.candidates) >= 2]
    exact_dupes = sum(r.exact_duplicate_count for r in alt_runs)
    all_overlaps = [o for r in alt_runs for o in r.segment_overlaps]
    per_scenario_medians = [
        statistics.median(r.segment_overlaps) for r in alt_runs if r.segment_overlaps
    ]
    distinct_scenarios = [
        r for r in alt_runs
        if any(o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP for o in r.segment_overlaps)
    ]
    all_distinct_scenarios = [
        r for r in alt_runs
        if all(o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP for o in r.segment_overlaps)
    ]

    return DiversitySummary(
        label=label,
        scenarios_with_alternatives=len(alt_runs),
        exact_duplicate_pairs=exact_dupes,
        median_pairwise_overlap=statistics.median(all_overlaps) if all_overlaps else None,
        median_of_scenario_medians=(
            statistics.median(per_scenario_medians) if per_scenario_medians else None
        ),
        pct_pairs_distinct=(
            100.0 * sum(1 for o in all_overlaps if o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP)
            / len(all_overlaps)
            if all_overlaps else None
        ),
        pct_scenarios_with_distinct_alt=(
            100.0 * len(distinct_scenarios) / len(alt_runs) if alt_runs else None
        ),
        pct_scenarios_all_distinct=(
            100.0 * len(all_distinct_scenarios) / len(alt_runs) if alt_runs else None
        ),
    )


# (attribute on CandidateReport, display label, format spec)
_NORMALIZED_METRICS: tuple[tuple[str, str, str], ...] = (
    ("radial_exposure", "radial exposure", "{:.3f}"),
    ("elongation_ratio", "elongation", "{:.2f}"),
    ("isoperimetric_quotient", "compactness", "{:.3f}"),
    ("distance_error_m", "distance error (m)", "{:.0f}"),
)


def _top_ranked_values(runs: list[GeneratorRun], attr: str) -> list[float]:
    """Value of the #1-ranked (roundest-first) candidate from every
    scenario that returned at least one -- the route a user would
    actually see first, one value per scenario."""
    return [getattr(r.candidates[0], attr) for r in runs if r.ok and r.candidates]


def _per_scenario_median_values(runs: list[GeneratorRun], attr: str) -> list[float]:
    """Each scenario's own median across ALL its returned candidates,
    one value per scenario -- every scenario weighs equally regardless
    of how many candidates it returned."""
    medians: list[float] = []
    for r in runs:
        if not r.ok or not r.candidates:
            continue
        medians.append(statistics.median(getattr(c, attr) for c in r.candidates))
    return medians


def _fmt(value: float | None, spec: str = "{:.3f}") -> str:
    return spec.format(value) if value is not None else "n/a"


def _scenario_normalized_section(
    title: str,
    description: str,
    v1_runs: list[GeneratorRun],
    v2_runs: list[GeneratorRun],
    values_fn: "Callable[[list[GeneratorRun], str], list[float]]",
) -> list[str]:
    lines = [f"### {title}", "", description, ""]
    lines.append("| metric | V1 median | V1 p95 | V2 median | V2 p95 |")
    lines.append("|---|---|---|---|---|")
    for attr, label, spec in _NORMALIZED_METRICS:
        v1_values = values_fn(v1_runs, attr)
        v2_values = values_fn(v2_runs, attr)
        lines.append(
            f"| {label} | {_fmt(_median(v1_values), spec)} | "
            f"{_fmt(_p95(v1_values), spec)} | {_fmt(_median(v2_values), spec)} | "
            f"{_fmt(_p95(v2_values), spec)} |"
        )
    lines.append("")
    return lines


def build_report(comparisons: list[ScenarioComparison]) -> str:
    v1_runs = [c.v1 for c in comparisons]
    v2_runs = [c.v2 for c in comparisons]
    v1_summary = _summarize("V1 (turnaround)", v1_runs)
    v2_summary = _summarize("V2 (polygon loop)", v2_runs)
    v1_diversity = _summarize_diversity("V1 (turnaround)", v1_runs)
    v2_diversity = _summarize_diversity("V2 (polygon loop)", v2_runs)

    v1_failed = [c for c in comparisons if not c.v1.ok]
    v2_failed = [c for c in comparisons if not c.v2.ok]

    lines: list[str] = []
    lines.append(
        f"# Polygon-loop V1 vs V2 comparison (PR #15) — "
        f"{datetime.now().isoformat(timespec='seconds')}"
    )
    lines.append("")
    lines.append(
        f"**Scope: `shape == \"round\"`, `amenity_required == False` "
        f"scenarios only -- {len(SCOPE_SCENARIOS)} of the full 537-scenario "
        f"suite (includes "
        f"{sum(1 for s in SCOPE_SCENARIOS if s.hard_case)} hard-case "
        f"scenarios).** `mix`/`out_and_back`/amenity-required scenarios "
        f"were NOT run through this comparison -- see module docstring "
        f"for why. Full-suite reliability numbers (all 537 scenarios, "
        f"all shapes) are unaffected and live in PR #14's "
        f"`report_20260820_122312.md`, which this script does not "
        f"overwrite."
    )
    lines.append("")
    lines.append(
        "V1 = `engine.generate_candidates(shape=\"round\")` (turnaround "
        "generator). V2 = `engine.generate_polygon_loop_candidates()` "
        "(PR #15 multi-anchor polygon-loop generator). Both scored with "
        "the identical `scripts.benchmark_suite._build_candidate_report` "
        "used by the PR #14 baseline."
    )
    lines.append("")
    lines.append(
        "**Note on the V1 short_start_return_spur rate below**: it is "
        "measured on THIS matched 91-scenario round/non-amenity subset, "
        "not PR #14's broader 176-candidate round population -- do not "
        "compare it directly to PR #14's 8.4% headline number."
    )
    lines.append("")
    lines.append("## Comparison (candidate-pooled)")
    lines.append("")
    lines.append(
        "Every returned candidate from every scenario, pooled together "
        "-- scenarios that returned more candidates contribute more "
        "weight. See \"Scenario-normalized geometry\" below for two "
        "per-scenario-weighted views of the same geometry metrics."
    )
    lines.append("")
    lines.append("| metric | V1 (turnaround) | V2 (polygon loop) |")
    lines.append("|---|---|---|")
    lines.append(
        f"| scenarios run | {v1_summary.scenarios_run} | {v2_summary.scenarios_run} |"
    )
    lines.append(
        f"| success rate (>=1 candidate) | {v1_summary.success_pct:.1f}% | "
        f"{v2_summary.success_pct:.1f}% |"
    )
    lines.append(
        f"| within +/-{TOLERANCE_M:.0f}m of target | "
        f"{v1_summary.within_tolerance_pct:.1f}% | "
        f"{v2_summary.within_tolerance_pct:.1f}% |"
    )
    lines.append(
        f"| latency median | {v1_summary.median_latency_s:.3f}s | "
        f"{v2_summary.median_latency_s:.3f}s |"
    )
    lines.append(
        f"| latency p95 | {v1_summary.p95_latency_s:.3f}s | "
        f"{v2_summary.p95_latency_s:.3f}s |"
    )
    lines.append(
        f"| candidates inspected | {v1_summary.candidate_count} | "
        f"{v2_summary.candidate_count} |"
    )
    lines.append(
        f"| radial_exposure median | {_fmt(v1_summary.median_radial_exposure)} | "
        f"{_fmt(v2_summary.median_radial_exposure)} |"
    )
    lines.append(
        f"| radial_exposure p95 | {_fmt(v1_summary.p95_radial_exposure)} | "
        f"{_fmt(v2_summary.p95_radial_exposure)} |"
    )
    lines.append(
        f"| elongation_ratio median | "
        f"{_fmt(v1_summary.median_elongation_ratio, '{:.2f}')} | "
        f"{_fmt(v2_summary.median_elongation_ratio, '{:.2f}')} |"
    )
    lines.append(
        f"| elongation_ratio p95 | "
        f"{_fmt(v1_summary.p95_elongation_ratio, '{:.2f}')} | "
        f"{_fmt(v2_summary.p95_elongation_ratio, '{:.2f}')} |"
    )
    lines.append(
        f"| isoperimetric_quotient (compactness) median | "
        f"{_fmt(v1_summary.median_isoperimetric_quotient)} | "
        f"{_fmt(v2_summary.median_isoperimetric_quotient)} |"
    )
    lines.append(
        f"| isoperimetric_quotient (compactness) p95 | "
        f"{_fmt(v1_summary.p95_isoperimetric_quotient)} | "
        f"{_fmt(v2_summary.p95_isoperimetric_quotient)} |"
    )
    lines.append(
        f"| excessive_repeated_segments rate | "
        f"{v1_summary.excessive_repeated_segments_pct:.1f}% | "
        f"{v2_summary.excessive_repeated_segments_pct:.1f}% |"
    )
    lines.append(
        f"| short_start_return_spur rate | "
        f"{v1_summary.short_start_return_spur_pct:.1f}% | "
        f"{v2_summary.short_start_return_spur_pct:.1f}% |"
    )
    lines.append(
        f"| disconnected rate | {v1_summary.disconnected_pct:.1f}% | "
        f"{v2_summary.disconnected_pct:.1f}% |"
    )
    lines.append("")

    lines.append("## Scenario-normalized geometry")
    lines.append("")
    lines.append(
        "The candidate-pooled view above lets scenarios with more "
        "candidates dominate. These two views weigh every scenario "
        "equally instead."
    )
    lines.append("")
    lines.extend(
        _scenario_normalized_section(
            "Top-ranked route per scenario",
            "Just the #1-ranked (roundest-first) candidate from each "
            "scenario -- the route a user would actually be shown "
            "first, one value per scenario.",
            v1_runs,
            v2_runs,
            _top_ranked_values,
        )
    )
    lines.extend(
        _scenario_normalized_section(
            "Per-scenario median, summarized across scenarios",
            "Each scenario's own median across ALL its returned "
            "candidates, then summarized across scenarios -- every "
            "scenario weighs equally regardless of candidate count.",
            v1_runs,
            v2_runs,
            _per_scenario_median_values,
        )
    )

    lines.append("## Candidate diversity (rendered-segment overlap)")
    lines.append("")
    lines.append(
        "Same methodology as `scripts.benchmark_suite`'s diversity "
        "section: each route is the set of undirected consecutive "
        "segments in its rendered geometry (6-decimal-rounded "
        "endpoints); pairwise overlap is the Jaccard index over those "
        "sets, for scenarios with >=2 candidates. "
        f"{DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f} overlap is the "
        "\"meaningfully distinct alternative\" reporting threshold "
        "(not a routing/scoring constant)."
    )
    lines.append("")
    lines.append(
        "| metric | V1 (turnaround) | V2 (polygon loop) |"
    )
    lines.append("|---|---|---|")
    lines.append(
        f"| scenarios with >=2 candidates | "
        f"{v1_diversity.scenarios_with_alternatives} | "
        f"{v2_diversity.scenarios_with_alternatives} |"
    )
    lines.append(
        f"| exact-duplicate candidate pairs | "
        f"{v1_diversity.exact_duplicate_pairs} | "
        f"{v2_diversity.exact_duplicate_pairs} |"
    )
    lines.append(
        f"| median pairwise overlap (pooled) | "
        f"{_fmt(v1_diversity.median_pairwise_overlap)} | "
        f"{_fmt(v2_diversity.median_pairwise_overlap)} |"
    )
    lines.append(
        f"| median of each scenario's median overlap | "
        f"{_fmt(v1_diversity.median_of_scenario_medians)} | "
        f"{_fmt(v2_diversity.median_of_scenario_medians)} |"
    )
    lines.append(
        f"| candidate pairs <= {DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f} overlap | "
        f"{_fmt(v1_diversity.pct_pairs_distinct, '{:.1f}%')} | "
        f"{_fmt(v2_diversity.pct_pairs_distinct, '{:.1f}%')} |"
    )
    lines.append(
        f"| scenarios with >=1 distinct alternative | "
        f"{_fmt(v1_diversity.pct_scenarios_with_distinct_alt, '{:.1f}%')} | "
        f"{_fmt(v2_diversity.pct_scenarios_with_distinct_alt, '{:.1f}%')} |"
    )
    lines.append(
        f"| scenarios where ALL pairs are distinct (no near-dupes at all) | "
        f"{_fmt(v1_diversity.pct_scenarios_all_distinct, '{:.1f}%')} | "
        f"{_fmt(v2_diversity.pct_scenarios_all_distinct, '{:.1f}%')} |"
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

    v2_zero_candidates = [c for c in comparisons if c.v2.ok and not c.v2.candidates]
    if v2_zero_candidates:
        lines.append(
            f"## V2 scenarios with zero candidates ({len(v2_zero_candidates)})"
        )
        lines.append("")
        for c in v2_zero_candidates:
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

    print(
        f"Running {len(SCOPE_SCENARIOS)} round/non-amenity scenarios "
        f"through V1 and V2 ...\n"
    )

    comparisons: list[ScenarioComparison] = []
    for i, scenario in enumerate(SCOPE_SCENARIOS):
        comparisons.append(run_scenario(graph, scenario))
        if (i + 1) % 20 == 0 or i == len(SCOPE_SCENARIOS) - 1:
            print(f"  ... {i + 1}/{len(SCOPE_SCENARIOS)} done")

    report_text = build_report(comparisons)
    print("\n" + report_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"polygon_loop_v1_vs_v2_{datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()
