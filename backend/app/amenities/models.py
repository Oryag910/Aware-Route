from dataclasses import dataclass
from typing import Literal


AmenityKind = Literal["restroom", "fountain"]


@dataclass(frozen=True)
class Amenity:
    lat: float
    lon: float
    kind: AmenityKind
    name: str | None
