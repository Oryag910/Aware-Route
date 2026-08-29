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

# Worst-case-ish ceiling for (sharp_turn_count + 2*u_turn_count) per km --
# used to normalize turn density into [0, 1] the same way
# SIGNALS_PER_KM_CEILING normalizes interruption density. U-turns count
# double since they're the out-and-back signature a "few turns" preset
# (tempo/intervals) most wants to avoid.
TURNS_PER_KM_CEILING = 6.0

# Renormalized from the original 15:10:5:5 ratio now that distance_error
# and mile_range_error are hard constraints instead of weighted factors.
# 15:10:5:5 reduces to 3:2:1:1 (dividing by 5), and 3+2+1+1 = 7, so each
# weight becomes its exact share of 7, preserving the original
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
# Shape metrics (sharp_turn_count, u_turn_count, compactness) were
# reported but deliberately not scored here -- repeated_segment_ratio
# already penalizes the dominant shape failure (out-and-backs), and
# shape was meant to become a preset-weighted factor in a later phase.
# That phase (workout presets, see WeightProfile/WEIGHT_PROFILES below)
# has now arrived: turn density and street-vs-path share join the
# composite as preset-driven factors, at a weight of 0 in the None
# (default) profile -- these six WEIGHT_* constants are that default
# profile's exact weights, kept standalone (rather than folded away)
# because the existing hand-computed default-weight tests import and
# assert against them directly.
WEIGHT_ELEVATION_MISMATCH = 3 / 10
WEIGHT_REPEATED_SEGMENT = 2 / 10
WEIGHT_INTERRUPTION = 2 / 10
WEIGHT_SIMILARITY_PENALTY = 1 / 10
WEIGHT_RESTROOM_CONFIDENCE = 1 / 10
WEIGHT_OFF_ROUTE = 1 / 10


@dataclass(frozen=True)
class WeightProfile:
    elevation: float
    repeated_segment: float
    interruption: float
    similarity: float
    restroom_confidence: float
    off_route: float
    turns: float
    street: float


# Each profile below is written as an integer-part ratio (elevation,
# repeated_segment, interruption, similarity, restroom_confidence,
# off_route, turns, street), then normalized by dividing every part by
# the row's sum -- the same "restate every weight as an exact share of
# the denominator" approach the pre-Phase-F comment above used for the
# six-factor composite.
#
#   profile     elev repeat interrupt sim conf off-route turns street sum
#   None(default)  3    2       2       1   1      1        0     0    10
#   easy            2    2       3       1   1      1        1     2    13
#   tempo           3    1       4       1   1      1        2     0    13
#   long             2    3       2       1   2      1        0     2    13
#   hills           5    1       1       1   1      1        0     1    11
#   intervals       2    0       4       1   1      1        3     0    12
#
# None is the pre-Phase-F default and MUST keep the exact 3:2:2:1:1:1
# ratio over 10 -- every existing hand-computed default-weight test
# depends on reproducing that composite unchanged.
WEIGHT_PROFILES: dict[str | None, WeightProfile] = {
    None: WeightProfile(
        elevation=3 / 10,
        repeated_segment=2 / 10,
        interruption=2 / 10,
        similarity=1 / 10,
        restroom_confidence=1 / 10,
        off_route=1 / 10,
        turns=0 / 10,
        street=0 / 10,
    ),
    "easy": WeightProfile(
        elevation=2 / 13,
        repeated_segment=2 / 13,
        interruption=3 / 13,
        similarity=1 / 13,
        restroom_confidence=1 / 13,
        off_route=1 / 13,
        turns=1 / 13,
        street=2 / 13,
    ),
    "tempo": WeightProfile(
        elevation=3 / 13,
        repeated_segment=1 / 13,
        interruption=4 / 13,
        similarity=1 / 13,
        restroom_confidence=1 / 13,
        off_route=1 / 13,
        turns=2 / 13,
        street=0 / 13,
    ),
    "long": WeightProfile(
        elevation=2 / 13,
        repeated_segment=3 / 13,
        interruption=2 / 13,
        similarity=1 / 13,
        restroom_confidence=2 / 13,
        off_route=1 / 13,
        turns=0 / 13,
        street=2 / 13,
    ),
    "hills": WeightProfile(
        elevation=5 / 11,
        repeated_segment=1 / 11,
        interruption=1 / 11,
        similarity=1 / 11,
        restroom_confidence=1 / 11,
        off_route=1 / 11,
        turns=0 / 11,
        street=1 / 11,
    ),
    "intervals": WeightProfile(
        elevation=2 / 12,
        repeated_segment=0 / 12,
        interruption=4 / 12,
        similarity=1 / 12,
        restroom_confidence=1 / 12,
        off_route=1 / 12,
        turns=3 / 12,
        street=0 / 12,
    ),
}


def turn_density_norm(
    sharp_turn_count: int,
    u_turn_count: int,
    route_km: float,
) -> float:
    if route_km <= 0.0:
        return 0.0

    turns_per_km = (
        sharp_turn_count + 2 * u_turn_count
    ) / route_km

    return min(turns_per_km / TURNS_PER_KM_CEILING, 1.0)


# Fallback candidates (those failing a hard constraint) are ranked by
# combined normalized distance+range error, weighted equally: a
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
    # "any" preference: the runner doesn't care about elevation, so this
    # factor is neutral -- no mismatch regardless of the route's profile.
    if preferred_bucket == "any":
        return 0.0

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
    via waypoint. Distance repair reshapes a loop into an out-and-back
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
    weights: WeightProfile,
) -> list[ScoredCandidate]:
    if not matched:
        return []

    # Subtotal excludes similarity, since similarity depends on the
    # ranking order this subtotal is used to establish (see below).
    subtotals = [
        weights.elevation * partial.elevation_mismatch
        + weights.repeated_segment * partial.repeated_segment_ratio
        + weights.interruption
        * interruption_norm(partial.signals_per_km)
        + weights.restroom_confidence
        * (1.0 - partial.restroom_confidence)
        + weights.off_route
        * off_route_norm(partial.off_route_distance_m)
        + weights.turns
        * turn_density_norm(
            partial.sharp_turn_count,
            partial.u_turn_count,
            partial.candidate.distance_m / 1000.0,
        )
        + weights.street * (1.0 - partial.pedestrian_path_ratio)
        for partial in matched
    ]

    # Similarity is computed against this provisional (7-factor) order
    # rather than searching all orderings; similarity's own weight
    # is small enough that this approximation is reasonable.
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
                + weights.similarity
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
    # entirely: the point of a fallback is "closest to what was
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
    workout_type: str | None = None,
) -> list[ScoredCandidate]:
    if workout_type not in WEIGHT_PROFILES:
        raise ValueError(f"Unknown workout_type: {workout_type!r}")

    weights = WEIGHT_PROFILES[workout_type]

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
        weights,
    )
    fallback_scored = _rank_fallback(
        [partial_scores[index] for index in fallback_indices],
        [distance_error_norms[index] for index in fallback_indices],
        [mile_range_error_norms[index] for index in fallback_indices],
    )

    return matched_scored + fallback_scored
