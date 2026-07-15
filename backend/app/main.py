from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.restrooms.models import Restroom
from app.restrooms.repository import (
    fetch_eligible_restrooms,
    get_supabase_client,
)
from app.restrooms.scoring import score_and_rank_candidates
from app.routing.candidates import get_loop_candidates
from app.routing.ors import OpenRouteServiceProvider
from app.routing.errors import (
    RouteNotFoundError,
    RoutingProviderError
)
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
    RoutingProvider,
)


app = FastAPI(title="Aware Running Route API")

routing_provider = OpenRouteServiceProvider()


class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    target_distance_m: Annotated[float, Field(gt=0)]
    count: Annotated[int, Field(ge=1, le=5)] = 3


class RestroomRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    target_distance_m: Annotated[float, Field(gt=0)]
    restroom_min_mile: Annotated[float, Field(ge=0)]
    restroom_max_mile: Annotated[float, Field(gt=0)]
    count: Annotated[int, Field(ge=1, le=5)] = 3


class RestroomInfo(BaseModel):
    facility_name: str
    status: str
    hours_of_operation: str | None
    latitude: float
    longitude: float
    mile_marker_m: float


class RankedRouteResponse(BaseModel):
    geometry: tuple[RoutePoint, ...]
    distance_m: float
    elevation_gain_m: float
    restroom: RestroomInfo
    distance_error_m: float
    mile_range_error_m: float


def get_routing_provider() -> RoutingProvider:
    return routing_provider


def get_eligible_restrooms() -> list[Restroom]:
    client = get_supabase_client()
    return fetch_eligible_restrooms(client)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/routes")
def get_routes(
    request: RouteRequest,
    provider: Annotated[
        RoutingProvider,
        Depends(get_routing_provider),
    ],
) -> list[RouteCandidate]:
    start = Coordinate(
        lat=request.start_lat,
        lon=request.start_lon,
    )

    try:
        return get_loop_candidates(
            provider,
            start,
            request.target_distance_m,
            request.count,
        )
    except RouteNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except RoutingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/routes/with-restroom")
def get_routes_with_restroom(
    request: RestroomRouteRequest,
    provider: Annotated[
        RoutingProvider,
        Depends(get_routing_provider),
    ],
    restrooms: Annotated[
        list[Restroom],
        Depends(get_eligible_restrooms),
    ],
) -> list[RankedRouteResponse]:
    start = Coordinate(
        lat=request.start_lat,
        lon=request.start_lon,
    )

    try:
        candidates = get_loop_candidates(
            provider,
            start,
            request.target_distance_m,
            request.count,
        )
    except RouteNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except RoutingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    scored_candidates = score_and_rank_candidates(
        candidates,
        restrooms,
        request.target_distance_m,
        request.restroom_min_mile * 1609.34,
        request.restroom_max_mile * 1609.34,
    )

    if not scored_candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "No candidate route passed an eligible restroom in range"
            ),
        )

    return [
        RankedRouteResponse(
            geometry=scored.candidate.geometry,
            distance_m=scored.candidate.distance_m,
            elevation_gain_m=scored.candidate.elevation_gain_m,
            restroom=RestroomInfo(
                facility_name=(
                    scored.restroom_match.restroom.facility_name
                ),
                status=scored.restroom_match.restroom.status,
                hours_of_operation=(
                    scored.restroom_match.restroom.hours_of_operation
                ),
                latitude=scored.restroom_match.restroom.latitude,
                longitude=scored.restroom_match.restroom.longitude,
                mile_marker_m=scored.restroom_match.mile_marker_m,
            ),
            distance_error_m=scored.distance_error_m,
            mile_range_error_m=scored.mile_range_error_m,
        )
        for scored in scored_candidates
    ]
