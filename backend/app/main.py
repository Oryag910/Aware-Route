from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.restrooms.models import Restroom
from app.restrooms.repository import (
    fetch_eligible_restrooms,
    get_supabase_client,
)
from app.restrooms.scoring import (
    best_restroom_waypoint,
    score_and_rank_candidates,
)
from app.restrooms.waypoint_candidates import (
    get_restroom_first_candidates,
    select_candidate_restrooms,
)
from app.routing.candidates import get_loop_candidates
from app.routing.ors import OpenRouteServiceProvider
from app.routing.errors import (
    RouteNotFoundError,
    RoutingProviderError
)
from app.routing.repair import RepairTarget, repair_near_miss_candidates
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
    RoutingProvider,
)


app = FastAPI(title="Aware Running Route API")

routing_provider = OpenRouteServiceProvider()

# Internal candidate pool size for /routes/with-restroom, independent of
# the request's `count` (which only controls how many *ranked* results
# are returned). Split between blind round_trip candidates and
# restroom-first candidates (routed through a chosen restroom as a
# waypoint, so the restroom-range axis isn't left to chance). Combined
# with the repair budget in routing/repair.py, worst case is
# 8 + 4 + 8 = 20 ORS calls/request (~100 requests/day on the free tier)
# -- unchanged from before this reallocation.
BLIND_CANDIDATE_COUNT = 8
RESTROOM_FIRST_CANDIDATE_LIMIT = 4


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
    elevation_preference: Literal["flat", "moderate", "hilly"]
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
    matched: bool
    off_route_distance_m: float
    distance_error_m: float
    mile_range_error_m: float
    distance_error_norm: float
    mile_range_error_norm: float
    elevation_mismatch: float
    repeated_segment_ratio: float
    restroom_confidence: float
    similarity_penalty: float
    composite_score: float


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
    min_mile_m = request.restroom_min_mile * 1609.34
    max_mile_m = request.restroom_max_mile * 1609.34

    try:
        candidates = get_loop_candidates(
            provider,
            start,
            request.target_distance_m,
            BLIND_CANDIDATE_COUNT,
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

    restroom_first = get_restroom_first_candidates(
        provider,
        start,
        restrooms,
        min_mile_m,
        max_mile_m,
        RESTROOM_FIRST_CANDIDATE_LIMIT,
    )

    # Every repair target carries a restroom as a via waypoint so
    # fixing distance can't silently drop the restroom. Repair reshapes
    # a loop into an out-and-back, which relocates every mile marker --
    # so the pinned restroom is chosen predictively (straight-line
    # closeness to the band midpoint, the same selection restroom-first
    # generation uses), not by its position on the soon-to-be-discarded
    # loop. Scoring's matched restroom is only a fallback for the rare
    # case where no restroom passes the straight-line filter.
    predictive_restrooms = select_candidate_restrooms(
        restrooms, start, min_mile_m, max_mile_m, 1
    )
    predictive_via = (
        Coordinate(
            lat=predictive_restrooms[0].latitude,
            lon=predictive_restrooms[0].longitude,
        )
        if predictive_restrooms
        else None
    )

    repair_targets = [
        RepairTarget(
            candidate=candidate,
            via=predictive_via
            or best_restroom_waypoint(
                candidate, restrooms, min_mile_m, max_mile_m
            ),
        )
        for candidate in candidates
    ] + [
        RepairTarget(
            candidate=entry.candidate, via=entry.restroom_waypoint
        )
        for entry in restroom_first
    ]

    repaired = repair_near_miss_candidates(
        provider,
        repair_targets,
        start,
        request.target_distance_m,
        min_mile_m,
        max_mile_m,
    )

    # Keep originals alongside their repaired versions rather than
    # replacing them -- a repaired route can win on distance yet lose
    # restroom placement, so scoring should get to pick from both.
    candidates = [target.candidate for target in repair_targets] + [
        candidate
        for candidate, target in zip(repaired, repair_targets)
        if candidate is not target.candidate
    ]

    scored_candidates = score_and_rank_candidates(
        candidates,
        restrooms,
        request.target_distance_m,
        min_mile_m,
        max_mile_m,
        preferred_elevation_bucket=request.elevation_preference,
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
            matched=scored.matched,
            off_route_distance_m=scored.off_route_distance_m,
            distance_error_m=scored.distance_error_m,
            mile_range_error_m=scored.mile_range_error_m,
            distance_error_norm=scored.distance_error_norm,
            mile_range_error_norm=scored.mile_range_error_norm,
            elevation_mismatch=scored.elevation_mismatch,
            repeated_segment_ratio=scored.repeated_segment_ratio,
            restroom_confidence=scored.restroom_confidence,
            similarity_penalty=scored.similarity_penalty,
            composite_score=scored.composite_score,
        )
        for scored in scored_candidates[: request.count]
    ]
