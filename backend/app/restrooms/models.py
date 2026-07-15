from dataclasses import dataclass


@dataclass(frozen=True)
class Restroom:
    source_id: str
    facility_name: str
    status: str
    latitude: float
    longitude: float
    hours_of_operation: str | None
    accessibility: str | None
    website: str | None
