from app.restrooms.archetypes import assign_archetypes
from app.restrooms.geo import RestroomMatch
from app.restrooms.models import Restroom
from app.restrooms.scoring import ScoredCandidate
from app.routing.provider import RouteCandidate, RoutePoint


def make_geometry(longitude: float) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(
            lat=40.70 + i * 0.001, lon=longitude, elevation_m=0.0
        )
        for i in range(5)
    )


def make_scored(
    longitude: float,
    *,
    matched: bool = True,
    signals_per_km: float = 1.0,
    longest_uninterrupted_m: float = 1000.0,
    pedestrian_path_ratio: float = 0.5,
    geometry: tuple[RoutePoint, ...] | None = None,
) -> ScoredCandidate:
    resolved_geometry = (
        geometry if geometry is not None else make_geometry(longitude)
    )
    restroom = Restroom(
        source_id=f"restroom-{longitude}",
        facility_name="Test Restroom",
        status="Operational",
        hours_of_operation=None,
        accessibility=None,
        website=None,
        latitude=40.70,
        longitude=longitude,
    )
    restroom_match = RestroomMatch(
        restroom=restroom,
        distance_to_route_m=10.0,
        mile_marker_m=1000.0,
    )
    candidate = RouteCandidate(
        geometry=resolved_geometry,
        distance_m=5000.0,
        elevation_gain_m=10.0,
    )

    return ScoredCandidate(
        candidate=candidate,
        restroom_match=restroom_match,
        distance_error_m=0.0,
        mile_range_error_m=0.0,
        off_route_distance_m=10.0,
        matched=matched,
        distance_error_norm=0.0,
        mile_range_error_norm=0.0,
        elevation_mismatch=0.0,
        repeated_segment_ratio=0.0,
        restroom_confidence=0.5,
        similarity_penalty=0.0,
        composite_score=0.0,
        signal_count=0,
        crossing_count=0,
        longest_uninterrupted_m=longest_uninterrupted_m,
        signals_per_km=signals_per_km,
        pedestrian_path_ratio=pedestrian_path_ratio,
        contains_stairs=False,
        sharp_turn_count=0,
        u_turn_count=0,
        compactness=0.5,
        smoothed_gain_m=10.0,
        climb_count=0,
        longest_climb_m=0.0,
        longest_climb_grade_pct=0.0,
        max_grade_pct=0.0,
    )


def test_three_distinct_matched_routes_get_all_three_labels() -> None:
    # Longitudes ~2km apart so no geometries share overlap cells.
    best = make_scored(-74.00, signals_per_km=3.0)
    smooth = make_scored(-74.02, signals_per_km=0.2)
    scenic = make_scored(
        -74.04, signals_per_km=5.0, pedestrian_path_ratio=0.9
    )

    labels = assign_archetypes([best, smooth, scenic])

    assert labels == ["best_overall", "smoothest", "most_scenic"]


def test_near_duplicate_of_best_gets_no_secondary_label() -> None:
    best = make_scored(-74.00)
    # Identical geometry to the best route -- overlap ratio 1.0, far
    # over the 0.7 cap, so it can't take "smoothest" even though its
    # signal density is lower.
    duplicate = make_scored(
        -74.00, signals_per_km=0.0, geometry=make_geometry(-74.00)
    )

    labels = assign_archetypes([best, duplicate])

    assert labels == ["best_overall", None]


def test_smoothest_picks_lowest_signal_density() -> None:
    best = make_scored(-74.00, signals_per_km=1.0)
    noisier = make_scored(-74.02, signals_per_km=4.0)
    quieter = make_scored(-74.04, signals_per_km=0.5)

    labels = assign_archetypes([best, noisier, quieter])

    assert labels[2] == "smoothest"


def test_unmatched_candidates_are_never_labeled() -> None:
    fallback_a = make_scored(-74.00, matched=False)
    fallback_b = make_scored(-74.02, matched=False)

    assert assign_archetypes([fallback_a, fallback_b]) == [None, None]


def test_first_matched_candidate_is_best_even_after_fallbacks() -> None:
    fallback = make_scored(-74.00, matched=False)
    matched = make_scored(-74.02)

    assert assign_archetypes([fallback, matched]) == [
        None,
        "best_overall",
    ]


def test_single_matched_candidate_only_gets_best_overall() -> None:
    assert assign_archetypes([make_scored(-74.00)]) == ["best_overall"]
