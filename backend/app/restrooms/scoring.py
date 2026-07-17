from dataclasses import dataclass

from app.flow.elevation import (
    detect_climbs,
    max_grade_pct,
    smoothed_gain_m,
)
from app.flow.interruptions import InterruptionStore, route_interruptions
from app.flow.shape import compactness, sharp_turn_count, u_turn_count
from app.flow.surfaces import contains_stairs, pedestrian_path_ratio
from app.restrooms.geo import (
    RESTROOM_PROXIMITY_THRESHOLD_M,
    RestroomMatch,
    match_restrooms_to_route,
)
from app.restrooms.models import Restroom
from app.restrooms.repeated_segments import repeated_segment_ratio
from app.restrooms.similarity import similarity_penalty_for_candidate
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint
from app.routing.repair import MAX_DISTANCE_ERROR_M


BASE_CONFIDENCE = 0.5
HOURS_PRESENT_BONUS = 0.3
ACCESSIBILITY_PRESENT_BONUS = 0.2

FLAT_MAX_GAIN_PER_KM = 10.0
MODERATE_MAX_GAIN_PER_KM = 25.0

BUCKET_ORDER = ("flat", "moderate", "hilly")

MAX_RESTROOM_RANGE_ERROR_M = 500.0

# Manhattan-grid-worst-case ceiling for signals_per_km -- used to
# normalize interruption density into [0, 1] alongside the other
# composite factors.
SIGNALS_PER_KM_CEILING = 8.0

# Renormalized from the original 15:10:5:5 ratio now that distance_error
# and mile_range_error are hard constraints instead of weighted factors.
# 15:10:5:5 reduces to 3:2:1:1 (dividing by 5), and 3+2+1+1 = 7, so each
# weight becomes its exact share of 7 — this preserves the original
# relative ratios among the four remaining factors. Off-route
# reachability was then added as a fifth factor at the same 1-part
# weight as similarity/restroom-confidence, so the denominator grows
# from 7 to 8 (3+2+1+1+1) and every weight is restated in eighths.
# Interruption density (traffic-signal frequency) was then added as a
# sixth factor at a 2-part weight -- on par with repeated_segment,
# since both are route-quality signals rather than restroom-fit
# signals -- so the ratio becomes 3:2:2:1:1:1 and the denominator grows
# from 8 to 10 (3+2+2+1+1+1), restating every weight in tenths.
# Crossing counts are reported but deliberately not scored -- OSM's
# crossing tagging is too noisy/inconsistent across boroughs to trust
# as a quality signal (many real crossings are untagged or tagged
# without a recognizable pattern), unlike traffic_signals which is
# reliably tagged.
# Shape metrics (sharp_turn_count, u_turn_count, compactness) are
# reported but deliberately not scored here -- repeated_segment_ratio
# already penalizes the dominant shape failure (out-and-backs), and
# shape is meant to become a preset-weighted factor in a later phase,
# not a fixed weight in this composite.
WEIGHT_ELEVATION_MISMATCH = 3 / 10
WEIGHT_REPEATED_SEGMENT = 2 / 10
WEIGHT_INTERRUPTION = 2 / 10
WEIGHT_SIMILARITY_PENALTY = 1 / 10
WEIGHT_RESTROOM_CONFIDENCE = 1 / 10
WEIGHT_OFF_ROUTE = 1 / 10

# Fallback candidates (those failing a hard constraint) are ranked by
# combined normalized distance+range error, weighted equally — a
# simpler ranking since the point of a fallback is "closest to what
# was asked," not route-quality nuance.
WEIGHT_FALLBACK_DISTANCE_ERROR = 0.5
WEIGHT_FALLBACK_MILE_RANGE_ERROR = 0.5


def off_route_norm(off_route_distance_m: float) -> float:
    return min(
        off_route_distance_m / RESTROOM_PROXIMITY_THRESHOLD_M,
        1.0,
    )


def interruption_norm(signals_per_km: float) -> float:
    return min(signals_per_km / SIGNALS_PER_KM_CEILING, 1.0)


def restroom_confidence(restroom: Restroom) -> float:
    confidence = BASE_CONFIDENCE

    if restroom.hours_of_operation:
        confidence += HOURS_PRESENT_BONUS

    if restroom.accessibility:
        confidence += ACCESSIBILITY_PRESENT_BONUS

    return min(confidence, 1.0)


def elevation_bucket(gain_per_km: float) -> str:
    if gain_per_km < FLAT_MAX_GAIN_PER_KM:
        return "flat"

    if gain_per_km <= MODERATE_MAX_GAIN_PER_KM:
        return "moderate"

    return "hilly"


def elevation_mismatch_norm(
    candidate: RouteCandidate,
    preferred_bucket: str,
) -> float:
    # Fewer than 2 geometry points means the smoothed profile can't be
    # computed (smooth_elevations/detect_climbs both degrade to
    # raw/empty) -- this keeps degenerate or synthetic candidates (e.g.
    # single-point test fixtures) working off the raw ORS total, same
    # as before this phase.
    if len(candidate.geometry) < 2:
        gain_m = candidate.elevation_gain_m
    else:
        gain_m = smoothed_gain_m(candidate.geometry)

    gain_per_km = gain_m / (candidate.distance_m / 1000.0)

    candidate_bucket = elevation_bucket(gain_per_km)

    candidate_index = BUCKET_ORDER.index(candidate_bucket)
    preferred_index = BUCKET_ORDER.index(preferred_bucket)

    mismatch = abs(candidate_index - preferred_index) / (
        len(BUCKET_ORDER) - 1
    )

    climb_count = len(detect_climbs(candidate.geometry))

    # A route whose *total* gain nets out flat can still contain a real
    # sustained climb (rolling terrain that returns to the same
    # elevation) -- that's not what a "flat" preference is asking for,
    # so floor the mismatch at "adjacent bucket" rather than reporting
    # a false 0. Symmetrically, a "hilly" preference wants an actual
    # climb, not total gain made of rolling/noise jitter with no single
    # sustained climb in it -- floor that case the same way.
    if preferred_bucket == "flat" and climb_count >= 1:
        mismatch = max(mismatch, 0.5)
    elif preferred_bucket == "hilly" and climb_count == 0:
        mismatch = max(mismatch, 0.5)

    return mismatch


def mile_range_error_m(
    mile_marker_m: float,
    min_mile_m: float,
    max_mile_m: float,
) -> float:
    if mile_marker_m < min_mile_m:
        return min_mile_m - mile_marker_m

    if mile_marker_m > max_mile_m:
        return mile_marker_m - max_mile_m

    return 0.0


def best_match_for_range(
    matches: list[RestroomMatch],
    min_mile_m: float,
    max_mile_m: float,
) -> RestroomMatch | None:
    if not matches:
        return None

    return min(
        matches,
        key=lambda match: mile_range_error_m(
            match.mile_marker_m,
            min_mile_m,
            max_mile_m,
        ),
    )


def best_restroom_waypoint(
    candidate: RouteCandidate,
    restrooms: list[Restroom],
    min_mile_m: float,
    max_mile_m: float,
) -> Coordinate | None:
    """The restroom a candidate would be scored against, as a repair
    via waypoint — distance repair reshapes a loop into an out-and-back
    through a nudged anchor, and without pinning the restroom the
    reshaped route routinely loses it (fixing one hard constraint by
    breaking the other)."""
    matches = match_restrooms_to_route(candidate.geometry, restrooms)
    best = best_match_for_range(matches, min_mile_m, max_mile_m)

    if best is None:
        return None

    return Coordinate(
        lat=best.restroom.latitude,
        lon=best.restroom.longitude,
    )


def normalize_min_max(values: list[float]) -> list[float]:
    if not values:
        return []

    lowest = min(values)
    highest = max(values)

    if highest == lowest:
        return [0.0 for _ in values]

    return [
        (value - lowest) / (highest - lowest)
        for value in values
    ]


@dataclass(frozen=True)
class _PartialScore:
    candidate: RouteCandidate
    restroom_match: RestroomMatch
    distance_error_m: float
    mile_range_error_m: float
    off_route_distance_m: float
    matched: bool
    repeated_segment_ratio: float
    elevation_mismatch: float
    restroom_confidence: float
    signal_count: int
    crossing_count: int
    longest_uninterrupted_m: float
    signals_per_km: float
    pedestrian_path_ratio: float
    contains_stairs: bool
    sharp_turn_count: int
    u_turn_count: int
    compactness: float
    smoothed_gain_m: float
    climb_count: int
    longest_climb_m: float
    longest_climb_grade_pct: float
    max_grade_pct: float


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: RouteCandidate
    restroom_match: RestroomMatch
    distance_error_m: float
    mile_range_error_m: float
    off_route_distance_m: float
    matched: bool
    distance_error_norm: float
    mile_range_error_norm: float
    elevation_mismatch: float
    repeated_segment_ratio: float
    restroom_confidence: float
    similarity_penalty: float
    composite_score: float
    signal_count: int
    crossing_count: int
    longest_uninterrupted_m: float
    signals_per_km: float
    pedestrian_path_ratio: float
    contains_stairs: bool
    sharp_turn_count: int
    u_turn_count: int
    compactness: float
    smoothed_gain_m: float
    climb_count: int
    longest_climb_m: float
    longest_climb_grade_pct: float
    max_grade_pct: float


def _rank_matched(
    matched: list[_PartialScore],
    distance_error_norms: list[float],
    mile_range_error_norms: list[float],
) -> list[ScoredCandidate]:
    if not matched:
        return []

    # Subtotal excludes similarity, since similarity depends on the
    # ranking order this subtotal is used to establish (see below).
    subtotals = [
        WEIGHT_ELEVATION_MISMATCH * partial.elevation_mismatch
        + WEIGHT_REPEATED_SEGMENT * partial.repeated_segment_ratio
        + WEIGHT_INTERRUPTION
        * interruption_norm(partial.signals_per_km)
        + WEIGHT_RESTROOM_CONFIDENCE
        * (1.0 - partial.restroom_confidence)
        + WEIGHT_OFF_ROUTE
        * off_route_norm(partial.off_route_distance_m)
        for partial in matched
    ]

    # Similarity is computed against this provisional (4-factor) order
    # rather than searching all orderings — similarity's own weight
    # (1/8) is small enough that this approximation is reasonable.
    provisional_order = sorted(
        range(len(matched)),
        key=lambda index: subtotals[index],
    )

    higher_ranked_geometries: list[tuple[RoutePoint, ...]] = []
    similarity_penalties: list[float] = [0.0] * len(matched)

    for index in provisional_order:
        geometry = matched[index].candidate.geometry

        similarity_penalties[index] = similarity_penalty_for_candidate(
            geometry,
            higher_ranked_geometries,
        )

        higher_ranked_geometries.append(geometry)

    scored_candidates = [
        ScoredCandidate(
            candidate=partial.candidate,
            restroom_match=partial.restroom_match,
            distance_error_m=partial.distance_error_m,
            mile_range_error_m=partial.mile_range_error_m,
            off_route_distance_m=partial.off_route_distance_m,
            matched=partial.matched,
            distance_error_norm=distance_error_norms[index],
            mile_range_error_norm=mile_range_error_norms[index],
            elevation_mismatch=partial.elevation_mismatch,
            repeated_segment_ratio=partial.repeated_segment_ratio,
            restroom_confidence=partial.restroom_confidence,
            similarity_penalty=similarity_penalties[index],
            composite_score=(
                subtotals[index]
                + WEIGHT_SIMILARITY_PENALTY
                * similarity_penalties[index]
            ),
            signal_count=partial.signal_count,
            crossing_count=partial.crossing_count,
            longest_uninterrupted_m=partial.longest_uninterrupted_m,
            signals_per_km=partial.signals_per_km,
            pedestrian_path_ratio=partial.pedestrian_path_ratio,
            contains_stairs=partial.contains_stairs,
            sharp_turn_count=partial.sharp_turn_count,
            u_turn_count=partial.u_turn_count,
            compactness=partial.compactness,
            smoothed_gain_m=partial.smoothed_gain_m,
            climb_count=partial.climb_count,
            longest_climb_m=partial.longest_climb_m,
            longest_climb_grade_pct=partial.longest_climb_grade_pct,
            max_grade_pct=partial.max_grade_pct,
        )
        for index, partial in enumerate(matched)
    ]

    scored_candidates.sort(
        key=lambda scored_candidate: scored_candidate.composite_score
    )

    return scored_candidates


def _rank_fallback(
    fallback: list[_PartialScore],
    distance_error_norms: list[float],
    mile_range_error_norms: list[float],
) -> list[ScoredCandidate]:
    # Fallback candidates skip the composite/similarity machinery
    # entirely — the point of a fallback is "closest to what was
    # asked," not route-quality nuance, so it's ranked purely by
    # combined normalized distance+range error.
    scored_candidates = [
        ScoredCandidate(
            candidate=partial.candidate,
            restroom_match=partial.restroom_match,
            distance_error_m=partial.distance_error_m,
            mile_range_error_m=partial.mile_range_error_m,
            off_route_distance_m=partial.off_route_distance_m,
            matched=partial.matched,
            distance_error_norm=distance_error_norms[index],
            mile_range_error_norm=mile_range_error_norms[index],
            elevation_mismatch=partial.elevation_mismatch,
            repeated_segment_ratio=partial.repeated_segment_ratio,
            restroom_confidence=partial.restroom_confidence,
            similarity_penalty=0.0,
            composite_score=(
                WEIGHT_FALLBACK_DISTANCE_ERROR
                * distance_error_norms[index]
                + WEIGHT_FALLBACK_MILE_RANGE_ERROR
                * mile_range_error_norms[index]
            ),
            signal_count=partial.signal_count,
            crossing_count=partial.crossing_count,
            longest_uninterrupted_m=partial.longest_uninterrupted_m,
            signals_per_km=partial.signals_per_km,
            pedestrian_path_ratio=partial.pedestrian_path_ratio,
            contains_stairs=partial.contains_stairs,
            sharp_turn_count=partial.sharp_turn_count,
            u_turn_count=partial.u_turn_count,
            compactness=partial.compactness,
            smoothed_gain_m=partial.smoothed_gain_m,
            climb_count=partial.climb_count,
            longest_climb_m=partial.longest_climb_m,
            longest_climb_grade_pct=partial.longest_climb_grade_pct,
            max_grade_pct=partial.max_grade_pct,
        )
        for index, partial in enumerate(fallback)
    ]

    scored_candidates.sort(
        key=lambda scored_candidate: scored_candidate.composite_score
    )

    return scored_candidates


def score_and_rank_candidates(
    candidates: list[RouteCandidate],
    restrooms: list[Restroom],
    target_distance_m: float,
    min_mile_m: float,
    max_mile_m: float,
    preferred_elevation_bucket: str,
    interruption_store: InterruptionStore,
) -> list[ScoredCandidate]:
    partial_scores: list[_PartialScore] = []

    for candidate in candidates:
        matches = match_restrooms_to_route(
            candidate.geometry,
            restrooms,
        )

        best_match = best_match_for_range(
            matches,
            min_mile_m,
            max_mile_m,
        )

        if best_match is None:
            continue

        distance_error = abs(
            candidate.distance_m - target_distance_m
        )

        range_error = mile_range_error_m(
            best_match.mile_marker_m,
            min_mile_m,
            max_mile_m,
        )

        interruptions = route_interruptions(
            candidate.geometry,
            interruption_store,
        )

        climbs = detect_climbs(candidate.geometry)
        longest_climb = (
            max(climbs, key=lambda climb: climb.length_m)
            if climbs
            else None
        )

        partial_scores.append(
            _PartialScore(
                candidate=candidate,
                restroom_match=best_match,
                distance_error_m=distance_error,
                mile_range_error_m=range_error,
                off_route_distance_m=best_match.distance_to_route_m,
                matched=(
                    distance_error <= MAX_DISTANCE_ERROR_M
                    and range_error <= MAX_RESTROOM_RANGE_ERROR_M
                ),
                repeated_segment_ratio=repeated_segment_ratio(
                    candidate.geometry
                ),
                elevation_mismatch=elevation_mismatch_norm(
                    candidate,
                    preferred_elevation_bucket,
                ),
                restroom_confidence=restroom_confidence(
                    best_match.restroom
                ),
                signal_count=interruptions.signal_count,
                crossing_count=interruptions.crossing_count,
                longest_uninterrupted_m=(
                    interruptions.longest_uninterrupted_m
                ),
                signals_per_km=interruptions.signals_per_km,
                pedestrian_path_ratio=pedestrian_path_ratio(
                    candidate.geometry,
                    candidate.extras,
                ),
                contains_stairs=contains_stairs(
                    candidate.geometry,
                    candidate.extras,
                ),
                sharp_turn_count=sharp_turn_count(candidate.geometry),
                u_turn_count=u_turn_count(candidate.geometry),
                compactness=compactness(candidate.geometry),
                smoothed_gain_m=smoothed_gain_m(candidate.geometry),
                climb_count=len(climbs),
                longest_climb_m=(
                    longest_climb.length_m
                    if longest_climb is not None
                    else 0.0
                ),
                longest_climb_grade_pct=(
                    longest_climb.avg_grade_pct
                    if longest_climb is not None
                    else 0.0
                ),
                max_grade_pct=max_grade_pct(candidate.geometry),
            )
        )

    if not partial_scores:
        return []

    distance_error_norms = normalize_min_max(
        [partial.distance_error_m for partial in partial_scores]
    )
    mile_range_error_norms = normalize_min_max(
        [partial.mile_range_error_m for partial in partial_scores]
    )

    matched_indices = [
        index
        for index, partial in enumerate(partial_scores)
        if partial.matched
    ]
    fallback_indices = [
        index
        for index, partial in enumerate(partial_scores)
        if not partial.matched
    ]

    matched_scored = _rank_matched(
        [partial_scores[index] for index in matched_indices],
        [distance_error_norms[index] for index in matched_indices],
        [mile_range_error_norms[index] for index in matched_indices],
    )
    fallback_scored = _rank_fallback(
        [partial_scores[index] for index in fallback_indices],
        [distance_error_norms[index] for index in fallback_indices],
        [mile_range_error_norms[index] for index in fallback_indices],
    )

    return matched_scored + fallback_scored
