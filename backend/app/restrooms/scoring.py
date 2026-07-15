from dataclasses import dataclass

from app.restrooms.geo import RestroomMatch, match_restrooms_to_route
from app.restrooms.models import Restroom
from app.routing.provider import RouteCandidate


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


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: RouteCandidate
    restroom_match: RestroomMatch
    distance_error_m: float
    mile_range_error_m: float


def score_and_rank_candidates(
    candidates: list[RouteCandidate],
    restrooms: list[Restroom],
    target_distance_m: float,
    min_mile_m: float,
    max_mile_m: float,
) -> list[ScoredCandidate]:
    scored_candidates: list[ScoredCandidate] = []

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

        scored_candidate = ScoredCandidate(
            candidate=candidate,
            restroom_match=best_match,
            distance_error_m=distance_error,
            mile_range_error_m=range_error,
        )

        scored_candidates.append(scored_candidate)

    scored_candidates.sort(
        key=lambda scored_candidate: (
            scored_candidate.mile_range_error_m,
            scored_candidate.distance_error_m,
        )
    )

    return scored_candidates
