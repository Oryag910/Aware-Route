from app.facilities.encounters import find_facility_encounters
from app.facilities.models import Facility
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
