from dataclasses import dataclass

from app.restrooms.geo import RestroomMatch, match_restrooms_to_route
from app.restrooms.models import Restroom
from app.restrooms.repeated_segments import repeated_segment_ratio
from app.restrooms.similarity import similarity_penalty_for_candidate
from app.routing.provider import RouteCandidate, RoutePoint
from app.routing.repair import MAX_DISTANCE_ERROR_M


BASE_CONFIDENCE = 0.5
HOURS_PRESENT_BONUS = 0.3
ACCESSIBILITY_PRESENT_BONUS = 0.2

FLAT_MAX_GAIN_PER_KM = 10.0
MODERATE_MAX_GAIN_PER_KM = 25.0

BUCKET_ORDER = ("flat", "moderate", "hilly")

MAX_RESTROOM_RANGE_ERROR_M = 500.0

# Renormalized from the original 15:10:5:5 ratio now that distance_error
# and mile_range_error are hard constraints instead of weighted factors.
# 15:10:5:5 reduces to 3:2:1:1 (dividing by 5), and 3+2+1+1 = 7, so each
# weight becomes its exact share of 7 — this preserves the original
# relative ratios among the four remaining factors.
WEIGHT_ELEVATION_MISMATCH = 3 / 7
WEIGHT_REPEATED_SEGMENT = 2 / 7
WEIGHT_SIMILARITY_PENALTY = 1 / 7
WEIGHT_RESTROOM_CONFIDENCE = 1 / 7

# Fallback candidates (those failing a hard constraint) are ranked by
# combined normalized distance+range error, weighted equally — a
# simpler ranking since the point of a fallback is "closest to what
# was asked," not route-quality nuance.
WEIGHT_FALLBACK_DISTANCE_ERROR = 0.5
WEIGHT_FALLBACK_MILE_RANGE_ERROR = 0.5


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
    gain_per_km = candidate.elevation_gain_m / (
        candidate.distance_m / 1000.0
    )

    candidate_bucket = elevation_bucket(gain_per_km)

    candidate_index = BUCKET_ORDER.index(candidate_bucket)
    preferred_index = BUCKET_ORDER.index(preferred_bucket)

    return abs(candidate_index - preferred_index) / (
        len(BUCKET_ORDER) - 1
    )


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
        + WEIGHT_RESTROOM_CONFIDENCE
        * (1.0 - partial.restroom_confidence)
        for partial in matched
    ]

    # Similarity is computed against this provisional (3-factor) order
    # rather than searching all orderings — similarity's own weight
    # (1/7) is small enough that this approximation is reasonable.
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
