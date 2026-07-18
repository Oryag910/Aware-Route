"""Rank locally-generated routes.

Consumes `GeneratedRoute`s (candidate + node_path + graph-native
`QualityMetrics`) from the local engine and ranks them. Hard constraints
gate `matched`; a quality score can never promote a route that fails a
hard constraint above one that passes. Soft factors are shape-aware --
e.g. edge reuse is a defect for a loop but the defining trait of an
out-and-back, so its weight is zeroed for that shape.

Deliberately separate from `app/restrooms/scoring.py` (the ORS scorer):
that one ranks ORS candidates with ORS-derived surface data, this one
ranks local candidates with graph-native metrics. The ORS scorer stays
untouched for the fallback path until ORS is retired.
"""

from dataclasses import dataclass
from typing import Literal

from app.amenities.matching import (
    AmenityMatch,
    best_amenity_for_range,
    match_amenities_to_route,
)
from app.amenities.models import Amenity
from app.flow.interruptions import InterruptionStore, route_interruptions
from app.generation.routes import GeneratedRoute
from app.restrooms.scoring import (
    BUCKET_ORDER,
    elevation_bucket,
    interruption_norm,
    mile_range_error_m,
    off_route_norm,
)
from app.restrooms.similarity import similarity_penalty_for_candidate
from app.routing.provider import RoutePoint


# Hard constraints (the plan's definition of a "matched" route).
MAX_DISTANCE_ERROR_M = 100.0
MAX_AMENITY_RANGE_ERROR_M = 500.0


ElevationPreference = Literal["flat", "moderate", "hilly"]


@dataclass(frozen=True)
class ShapeWeights:
    shape_match: float
    surface: float
    interruption: float
    elevation: float
    repetition: float
    amenity: float
    corrective_loop: float
    similarity: float


# Per-shape soft-factor weights; each row sums to 1.0. Rationale:
# - out_and_back zeroes `repetition` (retracing is the shape's defining
#   feature, not a defect) and folds that weight into surface quality.
# - `corrective_loop` is kept small everywhere: the detector also flags
#   an out-and-back's legitimate turnaround, so it must never dominate.
# - round/mix keep repetition meaningful (a loop that doubles back is a
#   real defect). These are reasonable hand-set defaults, not yet tuned
#   against human preference -- individually benchmarkable later.
SHAPE_WEIGHTS: dict[str, ShapeWeights] = {
    "round": ShapeWeights(
        shape_match=0.22,
        surface=0.18,
        interruption=0.14,
        elevation=0.14,
        repetition=0.10,
        amenity=0.10,
        corrective_loop=0.05,
        similarity=0.07,
    ),
    "mix": ShapeWeights(
        shape_match=0.15,
        surface=0.20,
        interruption=0.15,
        elevation=0.15,
        repetition=0.08,
        amenity=0.12,
        corrective_loop=0.08,
        similarity=0.07,
    ),
    "out_and_back": ShapeWeights(
        shape_match=0.22,
        surface=0.28,
        interruption=0.14,
        elevation=0.14,
        repetition=0.00,
        amenity=0.10,
        corrective_loop=0.05,
        similarity=0.07,
    ),
}


@dataclass(frozen=True)
class LocalScoredRoute:
    route: GeneratedRoute
    matched: bool
    best_amenity: AmenityMatch | None
    distance_error_m: float
    amenity_range_error_m: float
    off_route_distance_m: float
    # Soft-factor penalties (all 0..1, lower is better) -- returned per
    # route so callers can explain *why* a route ranked where it did,
    # not just show a composite.
    shape_match_penalty: float
    surface_penalty: float
    interruption_penalty: float
    elevation_penalty: float
    repetition_penalty: float
    amenity_penalty: float
    corrective_loop_penalty: float
    similarity_penalty: float
    signals_per_km: float
    elevation_bucket: str | None
    composite_score: float


def _shape_match_penalty(shape: str, isoperimetric_quotient: float) -> float:
    if shape == "round":
        # Want a round loop -> penalize low roundness.
        return 1.0 - isoperimetric_quotient
    if shape == "out_and_back":
        # Want a straight line -> penalize enclosed area.
        return isoperimetric_quotient
    return 0.0  # mix: shape is unconstrained.


def _elevation(
    elevation_gain_m: float | None,
    distance_m: float,
    preference: str,
) -> tuple[float, str | None]:
    """(penalty, bucket) for a route's elevation vs the preference.

    Neutral (0.0, None) when elevation is unavailable, so a graph without
    baked elevation degrades gracefully instead of biasing the ranking.
    """
    if elevation_gain_m is None or distance_m <= 0:
        return 0.0, None

    gain_per_km = elevation_gain_m / (distance_m / 1000.0)
    bucket = elevation_bucket(gain_per_km)
    penalty = abs(
        BUCKET_ORDER.index(bucket) - BUCKET_ORDER.index(preference)
    ) / (len(BUCKET_ORDER) - 1)
    return penalty, bucket


@dataclass(frozen=True)
class _Partial:
    route: GeneratedRoute
    matched: bool
    best_amenity: AmenityMatch | None
    distance_error_m: float
    amenity_range_error_m: float
    off_route_distance_m: float
    shape_match_penalty: float
    surface_penalty: float
    interruption_penalty: float
    elevation_penalty: float
    repetition_penalty: float
    amenity_penalty: float
    corrective_loop_penalty: float
    signals_per_km: float
    elevation_bucket: str | None
    subtotal: float  # weighted soft factors excluding similarity


def _partial_for(
    route: GeneratedRoute,
    amenities: list[Amenity],
    target_distance_m: float,
    min_range_m: float,
    max_range_m: float,
    elevation_preference: str,
    shape: str,
    weights: ShapeWeights,
    interruption_store: InterruptionStore,
) -> _Partial:
    candidate = route.candidate
    quality = route.quality

    matches = match_amenities_to_route(candidate.geometry, amenities)
    best = best_amenity_for_range(matches, min_range_m, max_range_m)

    distance_error = abs(candidate.distance_m - target_distance_m)
    if best is not None:
        amenity_range_error = mile_range_error_m(
            best.mile_marker_m, min_range_m, max_range_m
        )
        off_route = best.distance_to_route_m
    else:
        amenity_range_error = float("inf")
        off_route = float("inf")

    matched = (
        len(candidate.geometry) >= 2
        and distance_error <= MAX_DISTANCE_ERROR_M
        and amenity_range_error <= MAX_AMENITY_RANGE_ERROR_M
    )

    interruptions = route_interruptions(candidate.geometry, interruption_store)

    shape_penalty = _shape_match_penalty(
        shape, quality.isoperimetric_quotient
    )
    surface_penalty = 1.0 - quality.pedestrian_share
    interruption_penalty = interruption_norm(interruptions.signals_per_km)
    elevation_penalty, bucket = _elevation(
        quality.elevation_gain_m, candidate.distance_m, elevation_preference
    )
    repetition_penalty = quality.edge_reuse_ratio
    amenity_penalty = off_route_norm(off_route)
    corrective_penalty = quality.corrective_loop_penalty

    subtotal = (
        weights.shape_match * shape_penalty
        + weights.surface * surface_penalty
        + weights.interruption * interruption_penalty
        + weights.elevation * elevation_penalty
        + weights.repetition * repetition_penalty
        + weights.amenity * amenity_penalty
        + weights.corrective_loop * corrective_penalty
    )

    return _Partial(
        route=route,
        matched=matched,
        best_amenity=best,
        distance_error_m=distance_error,
        amenity_range_error_m=amenity_range_error,
        off_route_distance_m=off_route,
        shape_match_penalty=shape_penalty,
        surface_penalty=surface_penalty,
        interruption_penalty=interruption_penalty,
        elevation_penalty=elevation_penalty,
        repetition_penalty=repetition_penalty,
        amenity_penalty=amenity_penalty,
        corrective_loop_penalty=corrective_penalty,
        signals_per_km=interruptions.signals_per_km,
        elevation_bucket=bucket,
        subtotal=subtotal,
    )


def _finalize(
    partials: list[_Partial],
    weights: ShapeWeights,
) -> list[LocalScoredRoute]:
    """Add the rank-dependent similarity penalty and produce the final
    composite, walking partials in provisional (subtotal) order so each
    route's similarity is measured only against better-ranked ones."""
    order = sorted(range(len(partials)), key=lambda i: partials[i].subtotal)

    higher_ranked: list[tuple[RoutePoint, ...]] = []
    similarity: list[float] = [0.0] * len(partials)

    for index in order:
        geometry = partials[index].route.candidate.geometry
        similarity[index] = similarity_penalty_for_candidate(
            geometry, higher_ranked
        )
        higher_ranked.append(geometry)

    scored = [
        LocalScoredRoute(
            route=partial.route,
            matched=partial.matched,
            best_amenity=partial.best_amenity,
            distance_error_m=partial.distance_error_m,
            amenity_range_error_m=partial.amenity_range_error_m,
            off_route_distance_m=partial.off_route_distance_m,
            shape_match_penalty=partial.shape_match_penalty,
            surface_penalty=partial.surface_penalty,
            interruption_penalty=partial.interruption_penalty,
            elevation_penalty=partial.elevation_penalty,
            repetition_penalty=partial.repetition_penalty,
            amenity_penalty=partial.amenity_penalty,
            corrective_loop_penalty=partial.corrective_loop_penalty,
            similarity_penalty=similarity[index],
            signals_per_km=partial.signals_per_km,
            elevation_bucket=partial.elevation_bucket,
            composite_score=partial.subtotal
            + weights.similarity * similarity[index],
        )
        for index, partial in enumerate(partials)
    ]

    scored.sort(key=lambda s: s.composite_score)
    return scored


def score_local_routes(
    routes: list[GeneratedRoute],
    amenities: list[Amenity],
    target_distance_m: float,
    min_range_m: float,
    max_range_m: float,
    elevation_preference: ElevationPreference,
    shape: str,
    interruption_store: InterruptionStore,
    count: int,
) -> list[LocalScoredRoute]:
    """Rank routes, matched (pass both hard constraints) first.

    Matched routes are ranked by the shape-aware composite; fallback
    routes ("closest to the ask") by combined distance + amenity-range
    error. Returns up to `count`, matched before fallback so a quality
    score can never lift a constraint-failing route above a passing one.
    """
    weights = SHAPE_WEIGHTS.get(shape, SHAPE_WEIGHTS["mix"])

    partials = [
        _partial_for(
            route,
            amenities,
            target_distance_m,
            min_range_m,
            max_range_m,
            elevation_preference,
            shape,
            weights,
            interruption_store,
        )
        for route in routes
    ]

    matched = _finalize([p for p in partials if p.matched], weights)

    fallback_partials = [p for p in partials if not p.matched]
    fallback = _finalize(fallback_partials, weights)
    # Fallback is "closest to the ask": order by how far it misses the
    # hard constraints, not by route-quality nuance.
    fallback.sort(
        key=lambda s: (
            s.distance_error_m
            + min(s.amenity_range_error_m, 1.0e6)
        )
    )

    return (matched + fallback)[:count]
