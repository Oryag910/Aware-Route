"""Conditional facility catalog loading.

Builds the pool of `Facility` objects to route against, inferring which
data sources are actually needed from the requested `FacilityKind`s. A
`facility_requirements=[]` (or restroom-only / water-only) request must
never touch a loader for a kind nobody asked for -- see
`tests/facilities/test_catalog.py`, which asserts this by making the
unused loader raise.
"""

from collections.abc import Callable
from datetime import datetime

from app.amenities.fountains import Fountain
from app.restrooms.hours import confidently_closed
from app.restrooms.models import Restroom

from app.facilities.models import Facility, FacilityKind, FacilityRequirement


RestroomLoader = Callable[[], list[Restroom]]
WaterLoader = Callable[[], list[Fountain]]


def requested_kinds(
    requirements: list[FacilityRequirement],
) -> set[FacilityKind]:
    return {requirement.kind for requirement in requirements}


def restroom_to_facility(restroom: Restroom) -> Facility:
    return Facility(
        id=f"restroom:{restroom.source_id}",
        kind="restroom",
        lat=restroom.latitude,
        lon=restroom.longitude,
        name=restroom.facility_name,
        status=restroom.status,
        hours_of_operation=restroom.hours_of_operation,
        source="supabase",
    )


def fountain_to_facility(fountain: Fountain) -> Facility:
    return Facility(
        id=f"water:osm:{fountain.osm_id}",
        kind="water",
        lat=fountain.latitude,
        lon=fountain.longitude,
        name=fountain.name,
        # The committed OSM dataset carries no live operational signal --
        # null is honest, "Operational" would be a fabricated claim.
        status=None,
        hours_of_operation=None,
        source="osm",
    )


def load_facility_catalog(
    requirements: list[FacilityRequirement],
    *,
    restroom_loader: RestroomLoader,
    water_loader: WaterLoader,
    run_time: datetime,
) -> list[Facility]:
    """Load only the facility kinds actually requested.

    `restroom_loader`/`water_loader` are injected so callers (and tests)
    control exactly when/whether Supabase or the water dataset is touched.
    Restrooms are filtered by `confidently_closed` here, same as the
    legacy pipeline, so callers never see a closed restroom.
    """
    kinds = requested_kinds(requirements)
    facilities: list[Facility] = []

    if "restroom" in kinds:
        restrooms = restroom_loader()
        facilities.extend(
            restroom_to_facility(restroom)
            for restroom in restrooms
            if not confidently_closed(restroom, run_time)
        )

    if "water" in kinds:
        fountains = water_loader()
        facilities.extend(
            fountain_to_facility(fountain) for fountain in fountains
        )

    return facilities
