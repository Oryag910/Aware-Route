import os
from typing import cast

import httpx
from dotenv import load_dotenv
from supabase import Client, create_client


API_URL = "https://data.cityofnewyork.us/resource/i7jb-7jku.json"


def fetch_restrooms() -> list[dict[str, object]]:
    params: dict[str, str | int] = {
        "$select": "*,:id",
        "$where": ":@computed_region_yeji_bk3q='4'",
        "$limit": 1000,
    }

    response = httpx.get(API_URL, params=params, timeout=30.0)
    response.raise_for_status()

    data: list[dict[str, object]] = response.json()
    return data


def transform_record(
    record: dict[str, object],
) -> dict[str, str | float | None]:
    website: str | None = None
    website_value = record.get("website")

    if isinstance(website_value, dict):
        url_value = website_value.get("url")

        if isinstance(url_value, str):
            website = url_value

    return {
        "source_id": cast(str, record[":id"]),
        "facility_name": cast(str, record["facility_name"]),
        "location_type": cast(str | None, record.get("location_type")),
        "operator": cast(str | None, record.get("operator")),
        "status": cast(str, record["status"]),
        "seasonality": cast(str | None, record.get("open")),
        "hours_of_operation": cast(
            str | None,
            record.get("hours_of_operation"),
        ),
        "accessibility": cast(str | None, record.get("accessibility")),
        "restroom_type": cast(str | None, record.get("restroom_type")),
        "changing_stations": cast(
            str | None,
            record.get("changing_stations"),
        ),
        "additional_notes": cast(
            str | None,
            record.get("additional_notes"),
        ),
        "website": website,
        "latitude": float(cast(str, record["latitude"])),
        "longitude": float(cast(str, record["longitude"])),
        "borough": "Manhattan",
    }


def main() -> None:
    load_dotenv()

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    client: Client = create_client(supabase_url, supabase_key)

    restrooms = fetch_restrooms()
    transformed_restrooms = [
        transform_record(record) for record in restrooms
    ]

    result = (
        client.table("restrooms")
        .upsert(transformed_restrooms, on_conflict="source_id")
        .execute()
    )

    print(f"Upserted {len(result.data)} records")


if __name__ == "__main__":
    main()