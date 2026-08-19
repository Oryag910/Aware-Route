"""Full local-graph benchmark suite (Subagent B).

Runs every scenario in `scripts/benchmark_scenarios.py` against the REAL
Manhattan graph via `app.generation.engine.generate_candidates` /
`generate_amenity_aware`, measuring:
  - distance error vs target
  - offline fountain-placement pass (a bundled fountain node in the
    requested range actually appears on the returned geometry, same
    check as scripts/benchmark_amenities.py) -- this exercises the
    bundled fountain dataset only, not live Supabase restroom
    availability or the /routes/with-restroom restroom-match contract;
    those are covered by endpoint tests
  - latency
  - defect signals: excessive turns (sharp/U-turn counts from
    app.flow.shape), repeated-segment ratio (app.restrooms.repeated_segments),
    short start-return spur detection (a length-tuner-prepended
    out-and-back spur at the route start, distinct from a normal
    out_and_back route's turnaround), and connectivity/validity (dedup'd
    node path length vs geometry point count, is the geometry a single
    connected walk with no huge jumps)
  - route-segment diversity: undirected rendered-geometry segment
    overlap between a scenario's candidate alternatives

Graph-network constraint: routes are generated exclusively on the
committed OSMnx walk graph. This benchmark does not separately test for
ferry, motorway, or water-crossing defects; generated routes can only
use edges present in the walk-network artifact.

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

# A "short start-return spur" is the specific artifact
# app.generation.length_tune._spur_path introduces: when a candidate
# undershoots its target length, the tuner splices `spur +
# reversed(spur)` onto the FRONT of the route --
# `tuned_path = spur + node_path[1:]` -- so a genuine corrective spur
# always starts at the route's first geometry point and returns to the
# exact same graph node (bit-identical coordinate) a short distance
# later. This is deliberately narrower than "any short closed sub-path
# anywhere in the route": an earlier version of this detector scanned
# arbitrary (i, j) pairs and, on real Manhattan out_and_back routes,
# flagged ~96.6% of candidates -- but out_and_back routes are
# *constructed* as start -> turnaround -> reverse(outbound) -> start
# (app.generation.out_and_back.out_and_back_path), so any point within
# ~20-125m of the turnaround has a near-identical mirror point on the
# return leg a short traveled-distance away. That's the intended route
# shape, not a tuning artifact, and the old arbitrary-pair scan couldn't
# tell the two apart. Anchoring the scan at index 0 only (never scanning
# from the turnaround or any other interior point) targets exactly the
# tuner's prepended spur and nothing else.
#
# Measured as physical distance, not point-index gap: geometry points
# come from per-edge OSMnx polylines (app.graph.model.path_to_geometry),
# so point density varies with how curvy a street's geometry is -- a
# fixed index window conflates "many points" with "far apart".
#
# SHORT_SPUR_MIN_PATH_M: below this, two nearby points are just dense
# sampling along a curved OSM way (or adjacent-point noise), not a
# genuine out-and-back spur -- there's no real sub-path to speak of.
# SHORT_SPUR_MAX_PATH_M: keeps the documented ~250m "tiny" intent -- a
# spur this size or smaller is short relative to typical multi-mile
# routes; a longer return-to-start is a full out_and_back route, not a
# tuning artifact.
# SHORT_SPUR_START_REVISIT_RADIUS_M: how close a later point must land
# to geometry[0] to count as "the spur returned". Grounded in a full
# 537-scenario/2521-candidate run of this benchmark: candidates that
# genuinely contain a tuner-prepended spur return to *exactly* the start
# node (haversine == 0.0, since `_spur_path`'s out-and-back ends on the
# same graph node the route starts from, and both render via the same
# `node_coordinate` call) -- 163/2521 candidates landed in a tight
# [0, 0.001)m bucket. The next-closest naturally-occurring near-approach
# in that same run (ordinary street geometry happening to pass near the
# start without any spur) was ~13-14m, clustered around one specific
# scenario's local street layout. 1m sits cleanly in the gap between
# "exact spur return" and "coincidental nearby street", with generous
# margin on both sides.
SHORT_SPUR_MIN_PATH_M = 40.0
SHORT_SPUR_MAX_PATH_M = 250.0
SHORT_SPUR_START_REVISIT_RADIUS_M = 1.0

# Undirected route-segment overlap (Jaccard index over undirected
# rendered-geometry segment sets, see `_route_segment_signature` /
# `_segment_overlap`) between two candidates in the same scenario. Each
# route is represented as the set of undirected consecutive segments in
# its RENDERED geometry (app.graph.model.path_to_geometry), not the
# underlying OSMnx/NetworkX graph edges -- a single graph edge's
# LineString can expand into several rendered-geometry segments, so this
# is a proxy for path similarity, not a literal graph-edge-identity
# check. Coordinates are rounded to 6 decimal places (~0.11m at NYC's
# latitude) before forming segments, collapsing floating-point noise
# without merging distinct points.
SEGMENT_COORD_PRECISION = 6

# Reporting threshold (not a routing/scoring constant): a candidate pair
# at/below this overlap is considered a "meaningfully distinct"
# alternative for the purposes of the benchmark report.
DISTINCT_ALTERNATIVE_MAX_OVERLAP = 0.80

GEOJSON_SAMPLE_COUNT = 30

RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "local"


@dataclass(frozen=True)
class DefectFlags:
    excessive_sharp_turns: bool
    excessive_u_turns: bool
    excessive_repeated_segments: bool
    short_start_return_spur: bool
    disconnected: bool

    @property
    def any(self) -> bool:
        return (
            self.excessive_sharp_turns
            or self.excessive_u_turns
            or self.excessive_repeated_segments
            or self.short_start_return_spur
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
    # Pairwise undirected route-segment overlap for every candidate pair
    # in this scenario (empty when fewer than 2 candidates).
    segment_overlaps: tuple[float, ...] = ()

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

    @property
    def has_distinct_alternative(self) -> bool:
        """True if at least one candidate pair has segment overlap
        at/below DISTINCT_ALTERNATIVE_MAX_OVERLAP -- see that constant's
        docstring for what "distinct" means here."""
        return any(
            overlap <= DISTINCT_ALTERNATIVE_MAX_OVERLAP for overlap in self.segment_overlaps
        )


def _short_start_return_spur(geometry: tuple[Any, ...]) -> bool:
    """Detects a short out-and-back spur PREPENDED AT THE ROUTE START:
    the cumulative along-route distance traveled from geometry[0] to
    some later point j falls within [SHORT_SPUR_MIN_PATH_M,
    SHORT_SPUR_MAX_PATH_M] AND that point lands within
    SHORT_SPUR_START_REVISIT_RADIUS_M of geometry[0] -- i.e. the route
    left its start, went somewhere short, and came back, before
    continuing on. This mirrors exactly what
    app.generation.length_tune._spur_path splices onto the front of an
    undershooting candidate. See the module-level constants for why this
    only anchors at index 0 rather than scanning arbitrary (i, j) pairs.

    Physical-distance based, not point-index based -- geometry point
    density varies with per-edge OSMnx polyline curvature, so index gap
    alone doesn't measure on-the-ground spur size (see constants)."""
    from app.routing.geometry import haversine_m

    n = len(geometry)
    if n < 2:
        return False

    coords = [Coordinate(lat=p.lat, lon=p.lon) for p in geometry]
    start = coords[0]

    traveled_m = 0.0
    for j in range(1, n):
        traveled_m += haversine_m(coords[j - 1], coords[j])
        if traveled_m > SHORT_SPUR_MAX_PATH_M:
            break  # cumulative distance only grows -- no j beyond here can qualify
        if traveled_m < SHORT_SPUR_MIN_PATH_M:
            continue
        if haversine_m(start, coords[j]) <= SHORT_SPUR_START_REVISIT_RADIUS_M:
            return True

    return False


SegmentSignature = frozenset[tuple[tuple[float, float], tuple[float, float]]]


def _route_segment_signature(geometry: tuple[Any, ...]) -> SegmentSignature:
    """Undirected set of segments implied by a route's RENDERED geometry
    polyline (app.graph.model.path_to_geometry) -- not the underlying
    OSMnx/NetworkX graph edges. A single graph edge's LineString can
    expand into several rendered-geometry segments, so this is a proxy
    for path similarity, not a literal graph-edge-identity check.

    Each consecutive geometry pair becomes a segment, keyed by its
    endpoints rounded to SEGMENT_COORD_PRECISION decimals. Segments are
    stored as a sorted (min, max) tuple so A->B and B->A -- the same
    physical stretch of path walked in either direction -- collapse to
    one segment. Zero-length pairs (duplicate consecutive points, which
    can occur at route joins/spurs) are skipped since they carry no
    positional information and would otherwise inflate the "shared
    segments" count for two otherwise-different routes."""
    segments: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for a, b in zip(geometry, geometry[1:]):
        point_a = (round(a.lat, SEGMENT_COORD_PRECISION), round(a.lon, SEGMENT_COORD_PRECISION))
        point_b = (round(b.lat, SEGMENT_COORD_PRECISION), round(b.lon, SEGMENT_COORD_PRECISION))
        if point_a == point_b:
            continue
        segment = (point_a, point_b) if point_a <= point_b else (point_b, point_a)
        segments.add(segment)

    return frozenset(segments)


def _segment_overlap(segments_a: SegmentSignature, segments_b: SegmentSignature) -> float:
    """Undirected route-segment overlap: |A ∩ B| / |A ∪ B| (Jaccard
    index) over two routes' rendered-geometry segment sets. 0.0 = no
    shared rendered route segments, 1.0 = identical segment set
    (including the same route walked in reverse, since segments are
    undirected)."""
    union = segments_a | segments_b
    if not union:
        return 0.0
    return len(segments_a & segments_b) / len(union)


def _pairwise_segment_overlaps(candidates: list[RouteCandidate]) -> tuple[float, ...]:
    """Pairwise undirected route-segment overlap for every candidate
    pair in a scenario's result set (empty if fewer than 2 candidates)."""
    if len(candidates) < 2:
        return ()

    signatures = [_route_segment_signature(c.geometry) for c in candidates]
    return tuple(
        _segment_overlap(signatures[i], signatures[j])
        for i in range(len(signatures))
        for j in range(i + 1, len(signatures))
    )


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
        short_start_return_spur=_short_start_return_spur(candidate.geometry),
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
            segment_overlaps=_pairwise_segment_overlaps(candidates),
        ),
        candidates,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(round(pct / 100.0 * (len(sorted_values) - 1))))
    return sorted_values[index]


REPORT_SHAPES: tuple[str, ...] = ("round", "out_and_back", "mix")


@dataclass(frozen=True)
class ShapeQuality:
    shape: str
    candidate_count: int
    excessive_repeated_segments_pct: float
    short_start_return_spur_pct: float
    scenarios_with_alternatives: int
    median_segment_overlap: float | None
    distinct_alternative_pct: float | None
    distinct_alternative_count: int


def _shape_quality(ok_results: list[ScenarioResult], shape: str) -> ShapeQuality:
    bucket = [r for r in ok_results if r.scenario.shape == shape]
    candidates = [c for r in bucket for c in r.candidates]
    candidate_count = len(candidates)

    repeated_pct = (
        100.0 * sum(1 for c in candidates if c.defects.excessive_repeated_segments) / candidate_count
        if candidate_count
        else 0.0
    )
    spur_pct = (
        100.0 * sum(1 for c in candidates if c.defects.short_start_return_spur) / candidate_count
        if candidate_count
        else 0.0
    )

    alt_bucket = [r for r in bucket if len(r.candidates) >= 2]
    overlaps = [overlap for r in alt_bucket for overlap in r.segment_overlaps]
    median_overlap = statistics.median(overlaps) if overlaps else None
    distinct = [r for r in alt_bucket if r.has_distinct_alternative]
    distinct_pct = 100.0 * len(distinct) / len(alt_bucket) if alt_bucket else None

    return ShapeQuality(
        shape=shape,
        candidate_count=candidate_count,
        excessive_repeated_segments_pct=repeated_pct,
        short_start_return_spur_pct=spur_pct,
        scenarios_with_alternatives=len(alt_bucket),
        median_segment_overlap=median_overlap,
        distinct_alternative_pct=distinct_pct,
        distinct_alternative_count=len(distinct),
    )


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
        "short_start_return_spur": sum(
            1 for c in all_candidates if c.defects.short_start_return_spur
        ),
        "disconnected": sum(1 for c in all_candidates if c.defects.disconnected),
    }
    total_candidates = len(all_candidates)

    feasible_pass_pct = (
        100.0 * len(with_route) / len(feasible) if feasible else 0.0
    )

    alt_results = [r for r in ok_results if len(r.candidates) >= 2]
    all_overlaps = [overlap for r in alt_results for overlap in r.segment_overlaps]
    median_overlap = statistics.median(all_overlaps) if all_overlaps else None
    distinct_results = [r for r in alt_results if r.has_distinct_alternative]
    distinct_pct = (
        100.0 * len(distinct_results) / len(alt_results) if alt_results else 0.0
    )
    # All-pairs view: what fraction of every individual candidate pair
    # (not just "does the scenario have >=1 such pair") is at/below the
    # threshold -- a scenario with 5 near-duplicate candidates and one
    # outlier pair still passes the scenario-level check above, so this
    # gives a more honest picture of the whole candidate pool.
    all_pairs_distinct_pct = (
        100.0
        * sum(1 for overlap in all_overlaps if overlap <= DISTINCT_ALTERNATIVE_MAX_OVERLAP)
        / len(all_overlaps)
        if all_overlaps
        else 0.0
    )
    # Per-scenario median, then summarized across scenarios -- unlike
    # the pooled median above, this weights every scenario equally
    # instead of letting scenarios with more candidate pairs (e.g. 10
    # pairs at count=5 vs. 1 pair at count=2) dominate.
    per_scenario_medians = [
        statistics.median(r.segment_overlaps) for r in alt_results if r.segment_overlaps
    ]
    median_of_scenario_medians = (
        statistics.median(per_scenario_medians) if per_scenario_medians else None
    )

    shape_quality = {shape: _shape_quality(ok_results, shape) for shape in REPORT_SHAPES}

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
        f"- Scenarios with a meaningfully distinct alternative (>=1 candidate "
        f"pair with undirected route-segment overlap <= {DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f}, "
        f"scenarios w/ >=2 candidates): "
        f"**{distinct_pct:.1f}%** ({len(distinct_results)}/{len(alt_results)})"
    )
    lines.append("")
    lines.append("## Accuracy")
    lines.append("")
    lines.append(
        f"- Within +/-{TOLERANCE_M:.0f}m of target: {len(within_tol_results)}/"
        f"{len(ok_results)} ({within_tol_pct:.1f}%)"
    )
    lines.append(
        f"- Offline fountain-placement scenarios passing (fountain in "
        f"requested range AND on generated route): "
        f"{len(amenity_pass)}/{len(amenity_scenarios)} ({amenity_pass_pct:.1f}%)"
    )
    lines.append("")
    lines.append(
        "Methodology note: this benchmark validates local generation "
        "against the bundled fountain dataset. It does not validate live "
        "Supabase restroom availability or the /routes/with-restroom "
        "restroom-match contract; those are covered by endpoint tests."
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
    lines.append(
        "  (short_start_return_spur: a short out-and-back spur PREPENDED "
        "AT THE ROUTE START by the length tuner -- "
        f"{SHORT_SPUR_MIN_PATH_M:.0f}-{SHORT_SPUR_MAX_PATH_M:.0f}m of "
        f"travel from geometry[0] that returns within "
        f"{SHORT_SPUR_START_REVISIT_RADIUS_M:.0f}m of geometry[0]. Only "
        "scans from the route's start point, not arbitrary sub-paths, so "
        "it does not flag a normal out_and_back route's legitimate "
        "turnaround; see benchmark_suite._short_start_return_spur)"
    )
    lines.append("")
    lines.append(
        "Graph-network constraint: routes are generated exclusively on the "
        "committed OSMnx walk graph. This benchmark does not separately "
        "test for ferry, motorway, or water-crossing defects; generated "
        "routes can only use edges present in the walk-network artifact."
    )
    lines.append("")
    lines.append("## Diversity (undirected route-segment overlap)")
    lines.append("")
    lines.append(
        "Each route is represented as the set of undirected consecutive "
        "segments in its rendered geometry, after rounding endpoint "
        "coordinates to six decimal places (see `_route_segment_signature` "
        "/ `_segment_overlap`). Pairwise overlap is the Jaccard index over "
        "those segment sets, for scenarios with >=2 candidates. Note this "
        "is rendered-geometry segments, not underlying OSMnx/NetworkX "
        "graph edges -- a single graph edge's LineString can expand into "
        "several rendered segments. 0.0 = no shared rendered route "
        "segments between a pair, 1.0 = identical rendered segment set "
        "(including the same route walked in reverse)."
    )
    lines.append("")
    lines.append(
        f"- Median pairwise segment overlap (all candidate pairs pooled): "
        f"{f'{median_overlap:.3f}' if median_overlap is not None else 'n/a'}"
    )
    lines.append(
        f"- All candidate pairs at/below {DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f} "
        f"overlap: **{all_pairs_distinct_pct:.1f}%** "
        f"({sum(1 for o in all_overlaps if o <= DISTINCT_ALTERNATIVE_MAX_OVERLAP)}/"
        f"{len(all_overlaps)} pairs) -- every individual pair, not just "
        "\"does the scenario have >=1 such pair\"; a scenario can pass "
        "the per-scenario check below even if 4 of 5 candidates are "
        "near-duplicates, so this is the more honest whole-pool number."
    )
    lines.append(
        f"- Median of each scenario's own median pairwise overlap, "
        f"summarized across scenarios: "
        f"{f'{median_of_scenario_medians:.3f}' if median_of_scenario_medians is not None else 'n/a'} "
        "-- weights every scenario equally instead of letting "
        "scenarios with more candidate pairs dominate the pooled median "
        "above."
    )
    lines.append(
        f"- Scenarios with >=1 candidate pair at/below "
        f"{DISTINCT_ALTERNATIVE_MAX_OVERLAP:.2f} overlap (a reporting "
        f"threshold, not a routing/scoring constant): "
        f"**{distinct_pct:.1f}%** ({len(distinct_results)}/{len(alt_results)})"
    )
    lines.append("")
    lines.append(
        "Out-and-back routes structurally retrace most of their own "
        "outbound path on the return leg by definition, so their segment "
        "overlap is not directly comparable to round/mix routes -- see "
        "the shape breakdown below."
    )
    lines.append("")
    lines.append("## Shape-specific quality")
    lines.append("")
    lines.append(
        "100% repeated-segment reuse is expected by definition for "
        "out_and_back routes (the return leg retraces the outbound "
        "path); the same rate on a round route is a genuine defect "
        "signal."
    )
    lines.append("")
    lines.append(
        "| shape | candidates | excessive_repeated_segments | "
        "short_start_return_spur | scenarios w/ alternatives | "
        "median segment overlap | >=1 distinct pair |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for shape in REPORT_SHAPES:
        quality = shape_quality[shape]
        median_str = (
            f"{quality.median_segment_overlap:.3f}"
            if quality.median_segment_overlap is not None
            else "n/a"
        )
        distinct_str = (
            f"{quality.distinct_alternative_pct:.1f}% "
            f"({quality.distinct_alternative_count}/{quality.scenarios_with_alternatives})"
            if quality.distinct_alternative_pct is not None
            else "n/a"
        )
        lines.append(
            f"| {shape} | {quality.candidate_count} | "
            f"{quality.excessive_repeated_segments_pct:.1f}% | "
            f"{quality.short_start_return_spur_pct:.1f}% | "
            f"{quality.scenarios_with_alternatives} | {median_str} | "
            f"{distinct_str} |"
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
