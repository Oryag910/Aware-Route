import json
from pathlib import Path

from app.amenities.fountains import (
    Fountain,
    fountain_to_amenity,
    load_fountains,
)


def test_load_fountains_reads_tiny_json_file(tmp_path: Path) -> None:
    fountains_file = tmp_path / "fountains.json"
    fountains_file.write_text(
        json.dumps(
            {
                "fountains": [
                    {
                        "osm_id": 123,
                        "latitude": 40.75,
                        "longitude": -73.98,
                        "name": "Test Fountain",
                    },
                    {
                        "osm_id": 456,
                        "latitude": 40.76,
                        "longitude": -73.97,
                        "name": None,
                    },
                ]
            }
        )
    )

    fountains = load_fountains(fountains_file)

    assert fountains == [
        Fountain(osm_id=123, latitude=40.75, longitude=-73.98, name="Test Fountain"),
        Fountain(osm_id=456, latitude=40.76, longitude=-73.97, name=None),
    ]


def test_load_fountains_missing_file_returns_empty_list(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"

    assert load_fountains(missing) == []


def test_load_fountains_empty_fountains_key_returns_empty_list(
    tmp_path: Path,
) -> None:
    fountains_file = tmp_path / "fountains.json"
    fountains_file.write_text(json.dumps({"fountains": []}))

    assert load_fountains(fountains_file) == []


def test_fountain_to_amenity_maps_fields_and_kind() -> None:
    fountain = Fountain(
        osm_id=1, latitude=40.7, longitude=-73.9, name="Bryant Park"
    )

    amenity = fountain_to_amenity(fountain)

    assert amenity.lat == 40.7
    assert amenity.lon == -73.9
    assert amenity.kind == "fountain"
    assert amenity.name == "Bryant Park"
