from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.routing.candidates import get_loop_candidates
from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.ors import OpenRouteServiceProvider
from app.routing.provider import Coordinate, RouteCandidate, RoutingProvider


app = FastAPI()

_provider: RoutingProvider = OpenRouteServiceProvider()


class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    target_distance_m: Annotated[float, Field(gt=0)]
    count: Annotated[int, Field(ge=1, le=5)] = 3


def get_routing_provider() -> RoutingProvider:
    return _provider


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
            provider=provider,
            start=start,
            target_distance_m=request.target_distance_m,
            count=request.count,
        )

    except RouteNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except RoutingProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail="Routing provider is currently unavailable.",
        ) from exc