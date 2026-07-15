import os
from typing import Any, cast

from dotenv import load_dotenv
from supabase import Client, create_client

from app.restrooms.models import Restroom


def get_supabase_client() -> Client:
    load_dotenv()

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]

    return create_client(supabase_url, supabase_key)


def fetch_eligible_restrooms(
    client: Client,
) -> list[Restroom]:
    result = (
        client.table("restrooms")
        .select("*")
        .eq("status", "Operational")
        .execute()
    )

    rows = cast(list[dict[str, Any]], result.data)

    return [
        Restroom(
            source_id=cast(str, row["source_id"]),
            facility_name=cast(str, row["facility_name"]),
            status=cast(str, row["status"]),
            latitude=cast(float, row["latitude"]),
            longitude=cast(float, row["longitude"]),
            hours_of_operation=cast(
                str | None,
                row.get("hours_of_operation"),
            ),
            accessibility=cast(
                str | None,
                row.get("accessibility"),
            ),
            website=cast(
                str | None,
                row.get("website"),
            ),
        )
        for row in rows
    ]
