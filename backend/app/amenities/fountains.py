import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.amenities.models import Amenity


DEFAULT_FOUNTAINS_PATH = (
    Path(__file__).parents[2] / "data" / "fountains.json"
)


@dataclass(frozen=True)
class Fountain:
    osm_id: int
    latitude: float
    longitude: float
    name: str | None


def load_fountains(path: Path | None = None) -> list[Fountain]:
    fountains_path = path or DEFAULT_FOUNTAINS_PATH

    if not fountains_path.exists():
        return []

    with fountains_path.open() as fountains_file:
        data: dict[str, object] = json.load(fountains_file)

    raw_fountains = cast(list[dict[str, object]], data.get("fountains", []))

    return [
        Fountain(
            osm_id=int(cast(int, raw["osm_id"])),
            latitude=float(cast(float, raw["latitude"])),
            longitude=float(cast(float, raw["longitude"])),
            name=cast(str | None, raw.get("name")),
        )
        for raw in raw_fountains
    ]


_fountains: list[Fountain] | None = None


def get_fountains() -> list[Fountain]:
    global _fountains

    if _fountains is None:
        _fountains = load_fountains()

    return _fountains


def fountain_to_amenity(fountain: Fountain) -> Amenity:
    return Amenity(
        lat=fountain.latitude,
        lon=fountain.longitude,
        kind="fountain",
        name=fountain.name,
    )
