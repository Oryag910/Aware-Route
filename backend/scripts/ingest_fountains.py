import json
from pathlib import Path
from typing import cast

import httpx


# Manhattan bbox: (south, west, north, east).
MANHATTAN_BBOX = (40.68, -74.03, 40.88, -73.90)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OUTPUT_PATH = (
    Path(__file__).parents[1] / "data" / "fountains.json"
)

COORDINATE_DECIMALS = 5

# Overpass's default-instance load balancer 406s requests without a
# User-Agent (httpx sends none by default) -- any identifying value
# satisfies it.
REQUEST_HEADERS = {"User-Agent": "run-route-ingest/1.0"}


def build_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox

    return f"""
    [out:json][timeout:60];
    (
      node["amenity"="drinking_water"]({south},{west},{north},{east});
      way["amenity"="drinking_water"]({south},{west},{north},{east});
    );
    out body center;
    """


def fetch_elements(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, object]]:
    query = build_query(bbox)
    last_error: Exception | None = None

    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = httpx.post(
                endpoint,
                data={"data": query},
                timeout=90.0,
                headers=REQUEST_HEADERS,
            )
            response.raise_for_status()

            data: dict[str, object] = response.json()
            return cast(list[dict[str, object]], data["elements"])

        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
            print(f"Overpass endpoint {endpoint} failed: {exc}")
            continue

    raise RuntimeError(
        "All Overpass endpoints failed"
    ) from last_error


def transform_elements(
    elements: list[dict[str, object]],
) -> list[dict[str, object]]:
    fountains: list[dict[str, object]] = []

    for element in elements:
        tags = cast(dict[str, object], element.get("tags", {}))
        amenity = tags.get("amenity")

        if amenity != "drinking_water":
            continue

        # For nodes, use lat/lon directly. For ways, use center if available.
        lat_value = element.get("lat")
        lon_value = element.get("lon")

        # If not a node (no direct lat/lon), check for center (used in ways).
        if lat_value is None or lon_value is None:
            center = element.get("center", {})
            if isinstance(center, dict):
                lat_value = center.get("lat")
                lon_value = center.get("lon")

        if not isinstance(lat_value, (int, float)) or not isinstance(
            lon_value, (int, float)
        ):
            continue

        osm_id = element.get("id")
        name = tags.get("name")

        fountain = {
            "osm_id": osm_id,
            "latitude": round(float(lat_value), COORDINATE_DECIMALS),
            "longitude": round(float(lon_value), COORDINATE_DECIMALS),
            "name": name if isinstance(name, str) else None,
        }

        fountains.append(fountain)

    return fountains


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        elements = fetch_elements(MANHATTAN_BBOX)
        fountains = transform_elements(elements)

    except RuntimeError as exc:
        print(f"Ingest failed, writing empty store: {exc}")
        fountains = []

    with OUTPUT_PATH.open("w") as output_file:
        json.dump(
            {"fountains": fountains},
            output_file,
        )

    print(
        f"Wrote {len(fountains)} fountains "
        f"to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
