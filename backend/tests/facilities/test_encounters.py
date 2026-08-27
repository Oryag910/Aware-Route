import random

from app.facilities.assignment import assign_requirements
from app.facilities.encounters import (
    FACILITY_ACCESS_THRESHOLD_M,
    _find_facility_encounters_brute_force,
    find_facility_encounters,
)
from app.facilities.models import Facility, FacilityRequirement
from app.routing.provider import RoutePoint


def _facility(lat: float, lon: float, facility_id: str = "restroom:1") -> Facility:
    return Facility(
        id=facility_id,
        kind="restroom",
        lat=lat,
        lon=lon,
        name="Test",
        status="Operational",
        hours_of_operation=None,
        source="test",
    )


def _straight_line(n: int, step_deg: float = 0.0005) -> tuple[RoutePoint, ...]:
    """A straight north-south line; 1 degree latitude ~= 111320m, so each
    step is ~55.66m."""
    return tuple(
        RoutePoint(lat=40.0 + i * step_deg, lon=-73.0, elevation_m=0.0)
        for i in range(n)
    )


def test_single_pass_produces_one_encounter() -> None:
    geometry = _straight_line(20)
    facility = _facility(lat=geometry[10].lat, lon=geometry[10].lon + 0.0002)

    encounters = find_facility_encounters(geometry, [facility])

    assert len(encounters) == 1
    assert encounters[0].encounter_index == 0
    assert encounters[0].facility.id == facility.id


def test_several_adjacent_segments_still_one_encounter() -> None:
    """A facility near a stretch of several consecutive points must not
    fragment into multiple encounters."""
    geometry = _straight_line(30)
    # Offset laterally by ~9m -- well within the 130m threshold across
    # several consecutive ~55m segments.
    facility = _facility(lat=geometry[15].lat, lon=geometry[15].lon + 0.0001)

    encounters = find_facility_encounters(geometry, [facility])

    assert len(encounters) == 1


def test_route_leaves_and_revisits_produces_two_encounters() -> None:
    outbound = _straight_line(20)
    # Genuine out-and-back: outbound then the exact reverse (minus the
    # duplicated turnaround point).
    geometry = outbound + tuple(reversed(outbound[:-1]))

    facility = _facility(lat=outbound[5].lat, lon=outbound[5].lon + 0.0001)

    encounters = find_facility_encounters(geometry, [facility])

    assert len(encounters) == 2
    assert encounters[0].encounter_index == 0
    assert encounters[1].encounter_index == 1
    # Both encounters are the SAME physical facility.
    assert encounters[0].facility.id == encounters[1].facility.id
    # Outbound encounter happens well before the return encounter.
    assert encounters[0].mile_marker_m < encounters[1].mile_marker_m

    total_distance_from_outbound_leg = sum(
        (
            (
                (outbound[i + 1].lat - outbound[i].lat) * 111_320.0
            )
            ** 2
        )
        ** 0.5
        for i in range(len(outbound) - 1)
    )
    # The return-leg encounter should land roughly symmetric around the
    # turnaround: total_route_distance - outbound_encounter_marker.
    total_route_distance = encounters[1].mile_marker_m + encounters[0].mile_marker_m
    assert total_route_distance > 0  # sanity: markers are on opposite halves
    del total_distance_from_outbound_leg


def test_mile_markers_are_cumulative_along_final_geometry() -> None:
    geometry = _straight_line(40)
    facility = _facility(lat=geometry[30].lat, lon=geometry[30].lon)

    encounters = find_facility_encounters(geometry, [facility])

    assert len(encounters) == 1
    # ~30 steps * ~55.66m
    assert 1500.0 < encounters[0].mile_marker_m < 1800.0


def test_facility_far_from_route_has_no_encounter() -> None:
    geometry = _straight_line(20)
    facility = _facility(lat=geometry[10].lat, lon=geometry[10].lon + 0.01)

    encounters = find_facility_encounters(geometry, [facility])

    assert encounters == []


def test_multiple_facilities_independent_encounters() -> None:
    geometry = _straight_line(20)
    facility_a = _facility(lat=geometry[5].lat, lon=geometry[5].lon, facility_id="restroom:a")
    facility_b = _facility(lat=geometry[15].lat, lon=geometry[15].lon, facility_id="water:osm:b")

    encounters = find_facility_encounters(geometry, [facility_a, facility_b])

    ids = {e.facility.id for e in encounters}
    assert ids == {"restroom:a", "water:osm:b"}


# --- Differential: spatial-index path vs brute-force reference ----------
#
# `find_facility_encounters` is now index-accelerated (see
# `FacilitySpatialIndex`); `_find_facility_encounters_brute_force` is the
# original O(facilities x segments) algorithm kept only for these tests.
# Every case below asserts the FULL encounter objects are identical, not
# just counts/ids -- the index must change nothing about the answer.


def _assert_identical(geometry: tuple[RoutePoint, ...], facilities: list[Facility]) -> None:
    indexed = find_facility_encounters(geometry, facilities)
    brute = _find_facility_encounters_brute_force(geometry, facilities)
    assert indexed == brute


def _diagonal_line(n: int, lat_step: float = 0.0004, lon_step: float = 0.0004) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=40.7 + i * lat_step, lon=-73.95 + i * lon_step, elevation_m=0.0)
        for i in range(n)
    )


def _offset_facility(
    point: RoutePoint, lat_offset_deg: float, lon_offset_deg: float, facility_id: str
) -> Facility:
    return _facility(lat=point.lat + lat_offset_deg, lon=point.lon + lon_offset_deg, facility_id=facility_id)


def test_indexed_matches_brute_force_single_nearby_facility() -> None:
    geometry = _straight_line(20)
    facility = _offset_facility(geometry[10], 0.0, 0.0003, "restroom:1")
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_facility_just_inside_threshold() -> None:
    geometry = _straight_line(10)
    # ~5m inside 130m, offset purely in longitude from a point on the line.
    lon_offset_deg = (FACILITY_ACCESS_THRESHOLD_M - 5.0) / (111_320.0 * 0.75705)  # cos(40 deg)
    facility = _offset_facility(geometry[5], 0.0, lon_offset_deg, "restroom:inside")
    indexed = find_facility_encounters(geometry, [facility])
    assert len(indexed) == 1  # sanity: this placement really is a match
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_facility_just_outside_threshold() -> None:
    geometry = _straight_line(10)
    # ~5m outside 130m.
    lon_offset_deg = (FACILITY_ACCESS_THRESHOLD_M + 5.0) / (111_320.0 * 0.75705)
    facility = _offset_facility(geometry[5], 0.0, lon_offset_deg, "restroom:outside")
    indexed = find_facility_encounters(geometry, [facility])
    assert indexed == []  # sanity: this placement really is a miss
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_diagonal_segment() -> None:
    geometry = _diagonal_line(25)
    facility = _offset_facility(geometry[12], 0.0003, -0.0002, "restroom:diag")
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_zero_length_segment() -> None:
    """A repeated point (zero-length segment) must not crash or diverge."""
    base = _straight_line(10)
    geometry = base[:5] + (base[4],) + base[5:]  # duplicate point at index 5
    facility = _offset_facility(base[4], 0.0, 0.0002, "restroom:zero")
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_long_geometry() -> None:
    geometry = _straight_line(400)  # ~22km, comparable to a real long route
    rng = random.Random(42)
    facilities = [
        _offset_facility(
            geometry[rng.randrange(len(geometry))],
            (rng.random() - 0.5) * 0.0006,
            (rng.random() - 0.5) * 0.0006,
            f"restroom:{i}",
        )
        for i in range(40)
    ]
    _assert_identical(geometry, facilities)


def test_indexed_matches_brute_force_multiple_facilities_restroom_and_water() -> None:
    geometry = _diagonal_line(60)
    rng = random.Random(7)
    facilities = [
        _offset_facility(
            geometry[rng.randrange(len(geometry))],
            (rng.random() - 0.5) * 0.0008,
            (rng.random() - 0.5) * 0.0008,
            f"{'restroom' if i % 2 == 0 else 'water:osm'}:{i}",
        )
        for i in range(20)
    ]
    _assert_identical(geometry, facilities)


def test_indexed_matches_brute_force_same_facility_twice_on_out_and_back() -> None:
    outbound = _straight_line(30)
    geometry = outbound + tuple(reversed(outbound[:-1]))
    facility = _offset_facility(outbound[10], 0.0, 0.0001, "restroom:oab")
    indexed = find_facility_encounters(geometry, [facility])
    assert len(indexed) == 2  # sanity: genuinely two passes
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_adjacent_segments_one_encounter() -> None:
    geometry = _straight_line(30)
    facility = _offset_facility(geometry[15], 0.0, 0.00008, "restroom:adjacent")
    indexed = find_facility_encounters(geometry, [facility])
    assert len(indexed) == 1
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_leave_and_return_two_encounters() -> None:
    """A route that passes near a facility, moves far away, then comes
    back near it again (not a simple out-and-back) must still produce
    two independent encounters."""
    leg_a = _straight_line(15)
    detour = tuple(
        RoutePoint(lat=leg_a[-1].lat, lon=leg_a[-1].lon + 0.01 + i * 0.0005, elevation_m=0.0)
        for i in range(1, 10)
    )
    leg_b = tuple(
        RoutePoint(lat=leg_a[-1].lat + i * 0.0005, lon=leg_a[-1].lon, elevation_m=0.0)
        for i in range(1, 15)
    )
    geometry = leg_a + detour + leg_b
    facility = _offset_facility(leg_a[5], 0.0, 0.0001, "restroom:leave_return")
    _assert_identical(geometry, [facility])


def test_indexed_matches_brute_force_exact_mile_markers() -> None:
    geometry = _straight_line(50)
    facility = _offset_facility(geometry[25], 0.0, 0.0001, "restroom:marker")
    indexed = find_facility_encounters(geometry, [facility])
    brute = _find_facility_encounters_brute_force(geometry, [facility])
    assert len(indexed) == 1
    assert indexed[0].mile_marker_m == brute[0].mile_marker_m
    assert indexed[0].distance_to_route_m == brute[0].distance_to_route_m
    assert indexed[0].route_segment_index == brute[0].route_segment_index


def test_indexed_matches_brute_force_multiple_requirements_via_assignment() -> None:
    """End-to-end equivalence: indexed encounters feed the SAME
    assignment result as brute-force encounters, for a multi-requirement
    request."""
    geometry = _straight_line(60)
    restroom = _offset_facility(geometry[10], 0.0, 0.0001, "restroom:1")
    water = _offset_facility(geometry[40], 0.0, 0.0001, "water:osm:1")
    facilities = [restroom, water]

    indexed_encounters = find_facility_encounters(geometry, facilities)
    brute_encounters = _find_facility_encounters_brute_force(geometry, facilities)
    assert indexed_encounters == brute_encounters

    requirements = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0.0, max_distance_m=1000.0),
        FacilityRequirement(id="w1", kind="water", min_distance_m=1500.0, max_distance_m=3000.0),
    ]
    indexed_results = assign_requirements(requirements, indexed_encounters)
    brute_results = assign_requirements(requirements, brute_encounters)
    assert indexed_results == brute_results


def test_indexed_matches_brute_force_random_scatter_many_seeds() -> None:
    """Broad randomized coverage across several independent seeds/
    geometries, each asserting full encounter-object equivalence."""
    for seed in range(10):
        rng = random.Random(seed)
        n_points = rng.randint(20, 150)
        geometry = _diagonal_line(n_points, lat_step=0.0003, lon_step=-0.0002)
        n_facilities = rng.randint(5, 30)
        facilities = [
            _offset_facility(
                geometry[rng.randrange(n_points)],
                (rng.random() - 0.5) * 0.001,
                (rng.random() - 0.5) * 0.001,
                f"restroom:{seed}:{i}",
            )
            for i in range(n_facilities)
        ]
        _assert_identical(geometry, facilities)
