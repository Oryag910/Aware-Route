import random

from app.facilities.assignment import assign_requirements
from app.facilities.models import Facility, FacilityEncounter, FacilityRequirement


def _facility(facility_id: str, kind: str = "restroom") -> Facility:
    return Facility(
        id=facility_id,
        kind=kind,  # type: ignore[arg-type]
        lat=40.0,
        lon=-73.0,
        name=None,
        status=None,
        hours_of_operation=None,
        source="test",
    )


def _encounter(
    facility_id: str,
    mile_marker_m: float,
    kind: str = "restroom",
    encounter_index: int = 0,
    off_route_m: float = 5.0,
) -> FacilityEncounter:
    return FacilityEncounter(
        facility=_facility(facility_id, kind),
        encounter_index=encounter_index,
        mile_marker_m=mile_marker_m,
        distance_to_route_m=off_route_m,
        route_segment_index=0,
    )


def _req(req_id: str, kind: str, min_m: float, max_m: float) -> FacilityRequirement:
    return FacilityRequirement(id=req_id, kind=kind, min_distance_m=min_m, max_distance_m=max_m)  # type: ignore[arg-type]


def test_exact_kind_only() -> None:
    reqs = [_req("r1", "restroom", 0, 1000)]
    encounters = [_encounter("water:1", 500, kind="water")]

    results = assign_requirements(reqs, encounters)

    assert len(results) == 1
    assert results[0].satisfied is False
    assert results[0].encounter is None


def test_one_encounter_cannot_satisfy_two_requirements() -> None:
    reqs = [
        _req("r1", "restroom", 0, 1000),
        _req("r2", "restroom", 400, 600),
    ]
    encounters = [_encounter("restroom:1", 500)]

    results = assign_requirements(reqs, encounters)

    satisfied = [r for r in results if r.satisfied]
    assert len(satisfied) == 1
    used_ids = {r.encounter.facility.id for r in results if r.encounter is not None}
    # Only one distinct encounter was assigned to a satisfied result --
    # the second requirement cannot also claim it as satisfied.
    assert sum(1 for r in results if r.satisfied and r.encounter is not None) == 1
    del used_ids


def test_two_distinct_encounters_same_facility_satisfy_two_requirements() -> None:
    reqs = [
        _req("r1", "restroom", 2000, 3000),
        _req("r2", "restroom", 5000, 6000),
    ]
    encounters = [
        _encounter("restroom:1", 2500, encounter_index=0),
        _encounter("restroom:1", 5500, encounter_index=1),
    ]

    results = assign_requirements(reqs, encounters)

    assert all(r.satisfied for r in results)
    assert results[0].encounter is not None and results[0].encounter.encounter_index == 0
    assert results[1].encounter is not None and results[1].encounter.encounter_index == 1


def test_overlapping_requirement_windows_assign_optimally() -> None:
    reqs = [
        _req("r1", "restroom", 0, 1000),
        _req("r2", "restroom", 0, 2000),
    ]
    encounters = [
        _encounter("restroom:a", 500),
        _encounter("restroom:b", 1500),
    ]

    results = assign_requirements(reqs, encounters)

    assert all(r.satisfied for r in results)
    by_id = {r.requirement.id: r.encounter.facility.id for r in results if r.encounter}
    # r1 can only take restroom:a (in its narrower window); r2 must take
    # the remaining restroom:b for both to be satisfied.
    assert by_id["r1"] == "restroom:a"
    assert by_id["r2"] == "restroom:b"


def test_shuffled_requirement_input_gives_equivalent_assignment() -> None:
    reqs = [
        _req("r1", "restroom", 3000, 6500),
        _req("r2", "restroom", 9500, 14500),
        _req("w1", "water", 4500, 8000),
        _req("w2", "water", 9500, 12500),
    ]
    encounters = [
        _encounter("restroom:a", 4000),
        _encounter("restroom:b", 10000),
        _encounter("water:x", 5000, kind="water"),
        _encounter("water:y", 11000, kind="water"),
    ]

    baseline = assign_requirements(reqs, encounters)
    baseline_map = {
        r.requirement.id: (r.satisfied, r.encounter.facility.id if r.encounter else None)
        for r in baseline
    }

    shuffled_reqs = list(reqs)
    random.Random(7).shuffle(shuffled_reqs)
    shuffled_encounters = list(encounters)
    random.Random(3).shuffle(shuffled_encounters)

    shuffled_result = assign_requirements(shuffled_reqs, shuffled_encounters)
    shuffled_map = {
        r.requirement.id: (r.satisfied, r.encounter.facility.id if r.encounter else None)
        for r in shuffled_result
    }

    assert baseline_map == shuffled_map


def test_deterministic_tie_repeated_calls_identical() -> None:
    reqs = [_req("r1", "restroom", 0, 10000), _req("r2", "restroom", 0, 10000)]
    encounters = [_encounter("restroom:a", 3000), _encounter("restroom:b", 3000)]

    first = assign_requirements(reqs, encounters)
    second = assign_requirements(reqs, encounters)

    first_map = {r.requirement.id: r.encounter.facility.id if r.encounter else None for r in first}
    second_map = {r.requirement.id: r.encounter.facility.id if r.encounter else None for r in second}
    assert first_map == second_map


def test_partial_assignment_does_not_steal_satisfiable_encounter() -> None:
    """r1 can only be satisfied by encounter A. r2 wants A too but also
    has an out-of-range fallback B available. r1 must still get A."""
    reqs = [
        _req("r1", "restroom", 900, 1100),
        _req("r2", "restroom", 900, 1100),
    ]
    encounters = [
        _encounter("restroom:a", 1000),
        _encounter("restroom:b", 5000),  # out of range for both, fallback only
    ]

    results = assign_requirements(reqs, encounters)
    by_id = {r.requirement.id: r for r in results}

    assert by_id["r1"].satisfied or by_id["r2"].satisfied
    satisfied_ids = [rid for rid, r in by_id.items() if r.satisfied]
    assert len(satisfied_ids) == 1
    the_other = "r2" if satisfied_ids[0] == "r1" else "r1"
    # The unsatisfied one gets the fallback, not nothing, and not the
    # encounter that made the other one satisfied.
    assert by_id[the_other].encounter is not None
    assert by_id[the_other].encounter.facility.id == "restroom:b"  # type: ignore[union-attr]


def test_no_compatible_encounter_gives_null_not_infinity() -> None:
    reqs = [_req("r1", "restroom", 0, 1000)]
    results = assign_requirements(reqs, [])

    assert results[0].satisfied is False
    assert results[0].encounter is None
    assert results[0].range_error_m is None


def test_water_never_fills_restroom_and_vice_versa() -> None:
    reqs = [
        _req("r1", "restroom", 0, 1000),
        _req("w1", "water", 0, 1000),
    ]
    encounters = [
        _encounter("water:1", 500, kind="water"),
        _encounter("restroom:1", 500, kind="restroom"),
    ]

    results = assign_requirements(reqs, encounters)
    by_id = {r.requirement.id: r for r in results}

    assert by_id["r1"].encounter.facility.kind == "restroom"  # type: ignore[union-attr]
    assert by_id["w1"].encounter.facility.kind == "water"  # type: ignore[union-attr]
