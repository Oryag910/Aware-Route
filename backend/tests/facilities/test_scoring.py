from app.facilities.models import Facility, FacilityRequirement
from app.facilities.scoring import is_fully_valid, rank_key, score_facility_requirements
from app.routing.provider import RoutePoint


def _facility(facility_id: str, lat: float, lon: float, kind: str = "restroom") -> Facility:
    return Facility(
        id=facility_id,
        kind=kind,  # type: ignore[arg-type]
        lat=lat,
        lon=lon,
        name=None,
        status=None,
        hours_of_operation=None,
        source="test",
    )


def _line(n: int) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=40.0 + i * 0.0005, lon=-73.0, elevation_m=0.0) for i in range(n)
    )


def test_no_facility_requirements_scoring() -> None:
    geometry = _line(10)
    score = score_facility_requirements(geometry, [], [])
    assert score.all_satisfied is True
    assert score.requirements_total == 0
    assert is_fully_valid(geometry, distance_error_m=10.0, facility_score=score) is True


def test_all_requirements_and_distance_ok_fully_valid() -> None:
    geometry = _line(20)
    facility = _facility("restroom:1", geometry[10].lat, geometry[10].lon)
    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=2000)

    score = score_facility_requirements(geometry, [req], [facility])
    assert score.all_satisfied is True
    assert is_fully_valid(geometry, distance_error_m=10.0, facility_score=score) is True


def test_one_requirement_missing_not_fully_valid() -> None:
    geometry = _line(20)
    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=2000)

    score = score_facility_requirements(geometry, [req], [])
    assert score.all_satisfied is False
    assert is_fully_valid(geometry, distance_error_m=10.0, facility_score=score) is False


def test_water_never_fills_restroom_in_scoring() -> None:
    geometry = _line(20)
    facility = _facility("water:1", geometry[10].lat, geometry[10].lon, kind="water")
    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=2000)

    score = score_facility_requirements(geometry, [req], [facility])
    assert score.all_satisfied is False


def test_restroom_never_fills_water_in_scoring() -> None:
    geometry = _line(20)
    facility = _facility("restroom:1", geometry[10].lat, geometry[10].lon, kind="restroom")
    req = FacilityRequirement(id="w1", kind="water", min_distance_m=0, max_distance_m=2000)

    score = score_facility_requirements(geometry, [req], [facility])
    assert score.all_satisfied is False


def test_partial_routes_rank_below_fully_valid() -> None:
    geometry = _line(20)
    facility = _facility("restroom:1", geometry[10].lat, geometry[10].lon)
    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=2000)

    full_score = score_facility_requirements(geometry, [req], [facility])
    partial_score = score_facility_requirements(geometry, [req], [])

    full_key = rank_key(geometry, 10.0, full_score, quality_score=0.9)
    partial_key = rank_key(geometry, 10.0, partial_score, quality_score=0.0)

    assert full_key < partial_key


def test_out_and_back_retracing_not_punished_by_generic_scorer() -> None:
    """The generic scorer has no shape-defect notion at all -- that
    lives in route-quality scoring upstream, not here. Sanity check
    that scoring a route twice (simulating identical OAB legs) with the
    same inputs is stable and doesn't itself penalize repetition."""
    geometry = _line(10)
    score_a = score_facility_requirements(geometry, [], [])
    score_b = score_facility_requirements(geometry, [], [])
    assert score_a == score_b
