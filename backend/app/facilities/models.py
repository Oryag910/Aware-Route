"""Generic multi-facility domain model.

Replaces the single-restroom-range product contract with a variable-length,
typed set of facility requirements. `FacilityKind` is the new public product
vocabulary ("water", not the legacy "fountain"); `app/amenities/fountains.py`
keeps its own `Fountain`/`"fountain"` naming as a legacy data-source adapter,
mapped into `Facility(kind="water", ...)` at the catalog boundary
(`app/facilities/catalog.py`).
"""

from dataclasses import dataclass
from typing import Literal


FacilityKind = Literal["restroom", "water"]


@dataclass(frozen=True)
class Facility:
    """A physical facility with a stable, source-derived identity.

    IDs are namespaced by source so restrooms and water facilities can
    never collide: `restroom:<source_id>` / `water:osm:<osm_id>`. Stable
    identity (rather than a rounded-coordinate key) is what lets the same
    physical facility be recognized across multiple route encounters.
    """

    id: str
    kind: FacilityKind
    lat: float
    lon: float
    name: str | None
    status: str | None
    hours_of_operation: str | None
    source: str | None


@dataclass(frozen=True)
class FacilityRequirement:
    id: str
    kind: FacilityKind
    min_distance_m: float
    max_distance_m: float


@dataclass(frozen=True)
class SnappedFacility:
    facility: Facility
    node_id: int
    snap_distance_m: float


@dataclass(frozen=True)
class FacilityEncounter:
    """One distinct pass by a physical facility along a finished route.

    `encounter_index` is 0-based per facility id, ordered by
    `mile_marker_m` ascending, so the same physical facility passed twice
    (e.g. outbound + return on an out-and-back) produces two encounters
    with the same `facility.id` but different index/mile_marker.
    """

    facility: Facility
    encounter_index: int
    mile_marker_m: float
    distance_to_route_m: float
    route_segment_index: int


@dataclass(frozen=True)
class RequirementResult:
    requirement: FacilityRequirement
    satisfied: bool
    # None only when no compatible-kind encounter exists at all to
    # measure a miss against (`encounter` is then also None).
    range_error_m: float | None
    encounter: FacilityEncounter | None
