from datetime import datetime

import pytest

from app.facilities.catalog import load_facility_catalog, requested_kinds
from app.amenities.fountains import Fountain
from app.restrooms.models import Restroom
from app.facilities.models import FacilityRequirement


def _restroom(source_id: str = "r1", hours: str | None = None) -> Restroom:
    return Restroom(
        source_id=source_id,
        facility_name="Test Restroom",
        status="Operational",
        latitude=40.78,
        longitude=-73.96,
        hours_of_operation=hours,
        accessibility=None,
        website=None,
    )


def _fountain(osm_id: int = 1) -> Fountain:
    return Fountain(osm_id=osm_id, latitude=40.78, longitude=-73.96, name="Fountain")


def _raise() -> list[object]:
    raise RuntimeError("loader should not have been called")


def test_requested_kinds_empty() -> None:
    assert requested_kinds([]) == set()


def test_requested_kinds_mixed() -> None:
    reqs = [
        FacilityRequirement(id="a", kind="restroom", min_distance_m=0, max_distance_m=100),
        FacilityRequirement(id="b", kind="water", min_distance_m=0, max_distance_m=100),
    ]
    assert requested_kinds(reqs) == {"restroom", "water"}


def test_no_facility_request_never_calls_either_loader() -> None:
    result = load_facility_catalog(
        [],
        restroom_loader=_raise,  # type: ignore[arg-type]
        water_loader=_raise,  # type: ignore[arg-type]
        run_time=datetime.now(),
    )
    assert result == []


def test_water_only_never_calls_restroom_loader() -> None:
    reqs = [FacilityRequirement(id="w1", kind="water", min_distance_m=0, max_distance_m=1000)]
    result = load_facility_catalog(
        reqs,
        restroom_loader=_raise,  # type: ignore[arg-type]
        water_loader=lambda: [_fountain()],
        run_time=datetime.now(),
    )
    assert len(result) == 1
    assert result[0].kind == "water"
    assert result[0].id == "water:osm:1"
    assert result[0].status is None  # never fabricate operational status


def test_restroom_only_never_calls_water_loader() -> None:
    reqs = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    ]
    result = load_facility_catalog(
        reqs,
        restroom_loader=lambda: [_restroom()],
        water_loader=_raise,  # type: ignore[arg-type]
        run_time=datetime.now(),
    )
    assert len(result) == 1
    assert result[0].kind == "restroom"
    assert result[0].id == "restroom:r1"


def test_mixed_loads_both() -> None:
    reqs = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000),
        FacilityRequirement(id="w1", kind="water", min_distance_m=0, max_distance_m=1000),
    ]
    result = load_facility_catalog(
        reqs,
        restroom_loader=lambda: [_restroom()],
        water_loader=lambda: [_fountain()],
        run_time=datetime.now(),
    )
    kinds = {facility.kind for facility in result}
    assert kinds == {"restroom", "water"}


def test_restroom_loader_failure_propagates() -> None:
    def failing() -> list[Restroom]:
        raise RuntimeError("supabase down")

    reqs = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    ]
    with pytest.raises(RuntimeError):
        load_facility_catalog(
            reqs,
            restroom_loader=failing,
            water_loader=_raise,  # type: ignore[arg-type]
            run_time=datetime.now(),
        )


def test_confidently_closed_restroom_excluded() -> None:
    reqs = [
        FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    ]
    result = load_facility_catalog(
        reqs,
        restroom_loader=lambda: [_restroom(hours="9am - 5pm")],
        water_loader=_raise,  # type: ignore[arg-type]
        run_time=datetime(2026, 1, 1, 22, 0),
    )
    assert result == []
