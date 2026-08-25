"""Generic constraint-first scoring for facility-aware routes.

Independent of the old `local_scoring.py` (restroom-specific, one
`best_amenity`, hidden 500m success allowance). This scorer treats
distance and every facility requirement as hard constraints: a route is
FULLY VALID iff it has real geometry, its distance is within tolerance,
and EVERY requirement is strictly satisfied (mile marker inside its
requested window, not the old fuzzy allowance). Zero requirements means
facility validity is vacuously true -- only distance gates fully-valid.

Fully valid routes always outrank partial ones. Partial routes rank by
an explicit, deterministic tier order (see `rank_key`) so a prettier
route can never mask a failed hard constraint.
"""

from dataclasses import dataclass

from app.facilities.assignment import assign_requirements
from app.facilities.encounters import find_facility_encounters
from app.facilities.models import (
    Facility,
    FacilityRequirement,
    RequirementResult,
)
from app.routing.provider import RoutePoint


MAX_DISTANCE_ERROR_M = 100.0


@dataclass(frozen=True)
class FacilityScore:
    requirement_results: tuple[RequirementResult, ...]
    requirements_total: int
    requirements_satisfied_count: int
    all_satisfied: bool


def score_facility_requirements(
    geometry: tuple[RoutePoint, ...],
    requirements: list[FacilityRequirement],
    facilities: list[Facility],
) -> FacilityScore:
    if not requirements:
        return FacilityScore(
            requirement_results=(),
            requirements_total=0,
            requirements_satisfied_count=0,
            all_satisfied=True,
        )

    encounters = find_facility_encounters(geometry, facilities)
    results = assign_requirements(requirements, encounters)
    satisfied_count = sum(1 for r in results if r.satisfied)

    return FacilityScore(
        requirement_results=tuple(results),
        requirements_total=len(requirements),
        requirements_satisfied_count=satisfied_count,
        all_satisfied=satisfied_count == len(requirements),
    )


def is_fully_valid(
    geometry: tuple[RoutePoint, ...],
    distance_error_m: float,
    facility_score: FacilityScore,
) -> bool:
    return (
        len(geometry) >= 2
        and distance_error_m <= MAX_DISTANCE_ERROR_M
        and facility_score.all_satisfied
    )


_LARGE_RANGE_ERROR = 1.0e9


def rank_key(
    geometry: tuple[RoutePoint, ...],
    distance_error_m: float,
    facility_score: FacilityScore,
    quality_score: float,
) -> tuple[int, int, int, float, float, float, float]:
    """Ascending sort key -- lower is always better. Tiers, in order:

    1. fully valid (distance + every requirement satisfied)
    2. distance within tolerance
    3. most requirements satisfied
    4. smallest worst-single-requirement range error
    5. smallest total range error across requirements
    6. smallest absolute distance error
    7. route-quality score (soft factors)
    """
    fully_valid = is_fully_valid(geometry, distance_error_m, facility_score)
    within_tolerance = distance_error_m <= MAX_DISTANCE_ERROR_M

    range_errors = [
        r.range_error_m if r.range_error_m is not None else _LARGE_RANGE_ERROR
        for r in facility_score.requirement_results
    ]
    worst_range_error = max(range_errors) if range_errors else 0.0
    total_range_error = sum(range_errors) if range_errors else 0.0

    return (
        0 if fully_valid else 1,
        0 if within_tolerance else 1,
        -facility_score.requirements_satisfied_count,
        worst_range_error,
        total_range_error,
        distance_error_m,
        quality_score,
    )
