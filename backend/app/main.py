import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from app.amenities.fountains import Fountain, fountain_to_amenity, get_fountains
from app.amenities.models import Amenity
from app.amenities.snapping import snap_amenities
from app.facilities.catalog import load_facility_catalog
from app.facilities.models import FacilityKind, FacilityRequirement, RequirementResult
from app.facilities.orchestration import ConstrainedPlanner, ScoredRoute, plan_routes
from app.facilities.round_planner import plan_constrained_round
from app.flow.interruptions import (
    InterruptionStore,
    get_interruption_store as _get_interruption_store,
    route_interruptions,
)
from app.flow.shape import compactness, sharp_turn_count, u_turn_count
from app.generation.engine import generate_routes
from app.generation.local_scoring import LocalScoredRoute, score_local_routes
from app.graph.loader import get_graph
from app.rate_limit import rate_limit_dependency
from app.restrooms.archetypes import assign_archetypes
from app.restrooms.hours import confidently_closed
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
    RoutePoint,
    RoutingProvider,
)


logger = logging.getLogger(__name__)

# Route-generation engine: "ors" (default, the legacy OpenRouteService
# pipeline) or "local" (the in-process OSMnx walk graph). Set via env so
# switching -- or rolling back -- is a deploy-time flag, not a code change.
# Defaults to "ors" so production behaviour is unchanged until explicitly
# flipped.
ROUTING_ENGINE = os.environ.get("ROUTING_ENGINE", "ors").strip().lower()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load the walk graph once at boot when the local engine is active.

    Doing this eagerly (rather than lazily on the first request) means a
    broken/missing graph artifact surfaces as a single loud boot-time log
    line instead of a silent per-request ORS fallback, and no request ever
    pays the ~200MB load cost or races it. The load is best-effort: if it
    fails we log loudly but keep serving, since the per-request handler
    still has the ORS fallback as a safety net.
    """
    if ROUTING_ENGINE == "local":
        try:
            get_graph()
            logger.info("Local routing engine graph loaded at startup")
        except Exception:
            logger.exception(
                "ROUTING_ENGINE=local but the walk graph failed to load at "
                "startup; requests will fall back to ORS until this is fixed"
            )
    yield


app = FastAPI(title="Aware Running Route API", lifespan=lifespan)

# Dev relies on the Vite proxy, so no CORS is needed there -- only add
# the middleware when ALLOWED_ORIGINS is explicitly set (e.g. in
# production, pointing at the deployed frontend origin).
_allowed_origins = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

# ORS round_trip occasionally returns pathological loops (observed
# live: 29km and 723km for a 9.7km ask). They're far beyond repair's
# rescue band, so they'd only ever clutter the fallback ranking and
# displace genuinely-close candidates -- drop them outright.
MAX_CANDIDATE_DISTANCE_RATIO = 3.0

# Server-side abuse/performance safety ceiling on the generic /routes
# endpoint's facility_requirements list -- deliberately generous (not a
# low product-level assumption baked into the architecture, which is
# variable-length by construction) so it only ever protects
# infrastructure, never a real product ask.
MAX_FACILITY_REQUIREMENTS = 20

# Constrained planners /routes falls back to when natural matching alone
# doesn't yield enough fully valid candidates (see
# app/facilities/orchestration.py's plan_routes). Each is a no-op for
# shapes/requirements it doesn't apply to.
DEFAULT_CONSTRAINED_PLANNERS: list[ConstrainedPlanner] = [plan_constrained_round]


class RestroomRouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    target_distance_m: Annotated[float, Field(gt=0)]
    restroom_min_mile: Annotated[float, Field(ge=0)]
    restroom_max_mile: Annotated[float, Field(gt=0)]
    elevation_preference: Literal["flat", "moderate", "hilly", "any"]
    # Route shape, used only by the local engine (ROUTING_ENGINE=local);
    # the ORS pipeline ignores it. Defaults to "mix" so existing clients
    # that don't send it keep working.
    shape: Literal["round", "out_and_back", "mix"] = "mix"
    count: Annotated[int, Field(ge=1, le=5)] = 3
    # A naive (no timezone) datetime is treated as local wall-clock
    # time -- callers are expected to send local time, not UTC. None
    # means "now".
    run_time: datetime | None = None
    workout_type: Literal[
        "easy", "tempo", "long", "hills", "intervals"
    ] | None = None


class RestroomInfo(BaseModel):
    facility_name: str
    status: str
    hours_of_operation: str | None
    latitude: float
    longitude: float
    mile_marker_m: float
    # Which amenity kind this is, so the frontend can pick a distinct
    # marker. The ORS pipeline only matches Supabase restrooms, so it
    # always sends "restroom"; the local pipeline threads the real kind
    # (restrooms + water fountains). Defaults to "restroom" for back-compat.
    kind: Literal["restroom", "fountain"] = "restroom"


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
    signal_count: int
    crossing_count: int
    longest_uninterrupted_m: float
    signals_per_km: float
    pedestrian_path_ratio: float
    contains_stairs: bool
    sharp_turn_count: int
    u_turn_count: int
    compactness: float
    smoothed_gain_m: float
    climb_count: int
    longest_climb_m: float
    longest_climb_grade_pct: float
    max_grade_pct: float
    archetype: str | None


def get_routing_provider() -> RoutingProvider:
    """Constructs the ORS provider on demand.

    Never called on the local-engine happy path -- only from the ORS
    branch / local-fallback path of /routes/with-restroom -- so
    ORS_API_KEY is only required when ORS actually needs to run."""
    return OpenRouteServiceProvider()


def get_eligible_restrooms() -> list[Restroom]:
    """Fetch restrooms from Supabase, as a FastAPI dependency.

    Runs before the route handler body (and thus before routing-engine
    selection), so a Supabase outage here must fail loudly and cleanly
    rather than let an unhandled exception surface as an opaque 500 --
    or, worse, let the handler proceed as if restroom data were
    available. The detail message is deliberately generic: the raw
    exception can carry credentials/URLs in its message, so only the
    server-side log gets the real cause.
    """
    try:
        client = get_supabase_client()
        return fetch_eligible_restrooms(client)
    except Exception as exc:
        logger.exception(
            "Failed to fetch restroom data from Supabase; "
            "returning 503 to the client"
        )
        raise HTTPException(
            status_code=503,
            detail="Restroom data is temporarily unavailable",
        ) from exc


def get_interruption_store() -> InterruptionStore:
    return _get_interruption_store()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


def _local_response(
    scored: LocalScoredRoute,
    interruption_store: InterruptionStore,
    amenity_meta: dict[tuple[float, float], tuple[str, str, str | None]],
) -> RankedRouteResponse:
    """Map a locally-scored route onto the shared response contract so
    the frontend needs no changes. `scored.best_amenity` is guaranteed
    non-None by the caller (routes without an on-route amenity are
    dropped). Elevation-profile detail (per-climb grades) is not yet
    surfaced by the local engine, so those fields report neutral values;
    smoothed_gain_m carries the graph-node elevation gain."""
    candidate = scored.route.candidate
    quality = scored.route.quality
    geometry = candidate.geometry
    interruptions = route_interruptions(geometry, interruption_store)

    assert scored.best_amenity is not None
    amenity = scored.best_amenity.amenity
    key = (round(amenity.lat, 6), round(amenity.lon, 6))
    name, status, hours = amenity_meta.get(
        key, (amenity.name or "", "", None)
    )

    smoothed_gain = (
        quality.elevation_gain_m
        if quality.elevation_gain_m is not None
        else 0.0
    )

    return RankedRouteResponse(
        geometry=geometry,
        distance_m=candidate.distance_m,
        elevation_gain_m=smoothed_gain,
        restroom=RestroomInfo(
            facility_name=name,
            status=status,
            hours_of_operation=hours,
            latitude=amenity.lat,
            longitude=amenity.lon,
            mile_marker_m=scored.best_amenity.mile_marker_m,
            kind=amenity.kind,
        ),
        matched=scored.matched,
        off_route_distance_m=scored.off_route_distance_m,
        distance_error_m=scored.distance_error_m,
        mile_range_error_m=scored.amenity_range_error_m,
        distance_error_norm=0.0,
        mile_range_error_norm=0.0,
        elevation_mismatch=scored.elevation_penalty,
        repeated_segment_ratio=quality.edge_reuse_ratio,
        restroom_confidence=1.0,
        similarity_penalty=scored.similarity_penalty,
        composite_score=scored.composite_score,
        signal_count=interruptions.signal_count,
        crossing_count=interruptions.crossing_count,
        longest_uninterrupted_m=interruptions.longest_uninterrupted_m,
        signals_per_km=interruptions.signals_per_km,
        pedestrian_path_ratio=quality.pedestrian_share,
        contains_stairs=quality.waytype_breakdown.get("steps", 0.0) > 0.0,
        sharp_turn_count=sharp_turn_count(geometry),
        u_turn_count=u_turn_count(geometry),
        compactness=compactness(geometry),
        smoothed_gain_m=smoothed_gain,
        climb_count=0,
        longest_climb_m=0.0,
        longest_climb_grade_pct=0.0,
        max_grade_pct=0.0,
        archetype=None,
    )


def _build_local_responses(
    request: "RestroomRouteRequest",
    restrooms: list[Restroom],
    interruption_store: InterruptionStore,
) -> list[RankedRouteResponse]:
    """The in-process local-graph pipeline for /routes/with-restroom.

    Restrooms (from Supabase) and water fountains (from the ingested
    dataset) are treated as a single amenity pool; routes are generated
    through an in-range amenity, then ranked by the local scorer. Raises
    422 (honest no-match) when no route passes any eligible amenity in
    range -- the caller must NOT fall back to ORS on that, only on
    unexpected exceptions."""
    graph = get_graph()
    start = Coordinate(lat=request.start_lat, lon=request.start_lon)
    min_mile_m = request.restroom_min_mile * 1609.34
    max_mile_m = request.restroom_max_mile * 1609.34
    effective_run_time = request.run_time or datetime.now()

    open_restrooms = [
        restroom
        for restroom in restrooms
        if not confidently_closed(restroom, effective_run_time)
    ]
    fountains = get_fountains()

    amenities = [
        Amenity(
            lat=restroom.latitude,
            lon=restroom.longitude,
            kind="restroom",
            name=restroom.facility_name,
        )
        for restroom in open_restrooms
    ] + [fountain_to_amenity(fountain) for fountain in fountains]

    # (lat, lon) -> (name, status, hours) so the response can recover the
    # real facility metadata the generic Amenity doesn't carry.
    amenity_meta: dict[tuple[float, float], tuple[str, str, str | None]] = {}
    for restroom in open_restrooms:
        amenity_meta[
            (round(restroom.latitude, 6), round(restroom.longitude, 6))
        ] = (restroom.facility_name, restroom.status, restroom.hours_of_operation)
    for fountain in fountains:
        amenity_meta[
            (round(fountain.latitude, 6), round(fountain.longitude, 6))
        ] = (fountain.name or "Water fountain", "Operational", None)

    snapped = snap_amenities(graph, amenities, max_snap_m=200.0)

    routes = generate_routes(
        graph,
        start,
        request.target_distance_m,
        request.shape,
        request.count,
        snapped=snapped,
        min_range_m=min_mile_m,
        max_range_m=max_mile_m,
    )

    scored = score_local_routes(
        routes,
        amenities,
        request.target_distance_m,
        min_mile_m,
        max_mile_m,
        request.elevation_preference,
        request.shape,
        interruption_store,
        request.count,
    )

    with_amenity = [s for s in scored if s.best_amenity is not None]

    if not with_amenity:
        # Header set explicitly here (not via the `response` object) --
        # Starlette builds error responses from the raised exception, not
        # from headers mutated on the injected Response, so this is the
        # only way an error response can truthfully claim the local
        # engine produced it.
        raise HTTPException(
            status_code=422,
            detail="No candidate route passed an eligible restroom in range",
            headers={"X-Route-Engine": "local"},
        )

    return [
        _local_response(s, interruption_store, amenity_meta)
        for s in with_amenity
    ]


@app.post(
    "/routes/with-restroom",
    dependencies=[Depends(rate_limit_dependency)],
    deprecated=True,
)
def get_routes_with_restroom(
    request: RestroomRouteRequest,
    response: Response,
    restrooms: Annotated[
        list[Restroom],
        Depends(get_eligible_restrooms),
    ],
    interruption_store: Annotated[
        InterruptionStore,
        Depends(get_interruption_store),
    ],
) -> list[RankedRouteResponse]:
    # Records which engine actually served the response (visible in the
    # browser Network tab), so a silent ORS fallback can never masquerade
    # as the local engine.
    if ROUTING_ENGINE == "local":
        try:
            result = _build_local_responses(
                request, restrooms, interruption_store
            )
            response.headers["X-Route-Engine"] = "local"
            return result
        except HTTPException:
            # Honest no-match (422) or client error: return it, do NOT
            # fall back to ORS.
            raise
        except Exception:
            # Graph-load failure or an unexpected local-engine error:
            # fall back to the ORS pipeline below so the request still
            # succeeds while the local engine is stabilised.
            logger.exception(
                "Local routing engine failed; falling back to ORS"
            )

    response.headers["X-Route-Engine"] = "ors"

    # Only reached when ROUTING_ENGINE=ors, or the local engine raised
    # unexpectedly above -- so ORS is never constructed on the local
    # happy path, and ORS_API_KEY is only required here.
    provider = get_routing_provider()

    start = Coordinate(
        lat=request.start_lat,
        lon=request.start_lon,
    )
    min_mile_m = request.restroom_min_mile * 1609.34
    max_mile_m = request.restroom_max_mile * 1609.34
    effective_run_time = request.run_time or datetime.now()

    # Filtering here -- before restroom-first candidate selection,
    # repair via waypoints, and scoring -- means one filter covers
    # every downstream use of `restrooms`.
    restrooms = [
        restroom
        for restroom in restrooms
        if not confidently_closed(restroom, effective_run_time)
    ]

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

    candidates = [
        candidate
        for candidate in candidates
        if candidate.distance_m
        <= MAX_CANDIDATE_DISTANCE_RATIO * request.target_distance_m
    ]

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
        interruption_store=interruption_store,
        workout_type=request.workout_type,
    )

    if not scored_candidates:
        raise HTTPException(
            status_code=422,
            detail=(
                "No candidate route passed an eligible restroom in range"
            ),
        )

    sliced_candidates = scored_candidates[: request.count]
    archetypes = assign_archetypes(sliced_candidates)

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
            signal_count=scored.signal_count,
            crossing_count=scored.crossing_count,
            longest_uninterrupted_m=scored.longest_uninterrupted_m,
            signals_per_km=scored.signals_per_km,
            pedestrian_path_ratio=scored.pedestrian_path_ratio,
            contains_stairs=scored.contains_stairs,
            sharp_turn_count=scored.sharp_turn_count,
            u_turn_count=scored.u_turn_count,
            compactness=scored.compactness,
            smoothed_gain_m=scored.smoothed_gain_m,
            climb_count=scored.climb_count,
            longest_climb_m=scored.longest_climb_m,
            longest_climb_grade_pct=scored.longest_climb_grade_pct,
            max_grade_pct=scored.max_grade_pct,
            archetype=archetype,
        )
        for scored, archetype in zip(sliced_candidates, archetypes)
    ]


# ---------------------------------------------------------------------------
# Generic multi-facility /routes endpoint (PR #18).
#
# Replaces the single-restroom-range contract with a variable-length,
# typed list of facility requirements. Local-engine only -- the ORS
# pipeline cannot truthfully honor an arbitrary set of typed cumulative-
# mile stops, so this endpoint never falls back to ORS (see module intro
# in app/facilities/orchestration.py). /routes/with-restroom above keeps
# its existing ORS-fallback behavior unchanged for backward compatibility.
# ---------------------------------------------------------------------------


class FacilityRequirementIn(BaseModel):
    id: Annotated[str, Field(min_length=1)]
    kind: FacilityKind
    min_distance_m: Annotated[float, Field(ge=0)]
    max_distance_m: Annotated[float, Field(gt=0)]


class RouteRequest(BaseModel):
    start_lat: float
    start_lon: float
    target_distance_m: Annotated[float, Field(gt=0)]
    facility_requirements: Annotated[
        list[FacilityRequirementIn],
        Field(default_factory=list, max_length=MAX_FACILITY_REQUIREMENTS),
    ]
    elevation_preference: Literal["flat", "moderate", "hilly", "any"] = "any"
    shape: Literal["round", "out_and_back", "mix"] = "mix"
    count: Annotated[int, Field(ge=1, le=5)] = 3
    run_time: datetime | None = None
    workout_type: Literal[
        "easy", "tempo", "long", "hills", "intervals"
    ] | None = None

    @model_validator(mode="after")
    def _validate_requirements(self) -> "RouteRequest":
        ids = [requirement.id for requirement in self.facility_requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("facility_requirements ids must be unique")

        for requirement in self.facility_requirements:
            if requirement.max_distance_m <= requirement.min_distance_m:
                raise ValueError(
                    f"requirement {requirement.id}: max_distance_m must be "
                    "greater than min_distance_m"
                )
            if requirement.max_distance_m > self.target_distance_m:
                raise ValueError(
                    f"requirement {requirement.id}: max_distance_m must be "
                    "<= target_distance_m"
                )

        return self


class FacilityOut(BaseModel):
    id: str
    kind: FacilityKind
    name: str | None
    status: str | None
    hours_of_operation: str | None
    latitude: float
    longitude: float
    mile_marker_m: float
    off_route_distance_m: float
    encounter_index: int


class FacilityResultOut(BaseModel):
    requirement_id: str
    kind: FacilityKind
    requested_min_m: float
    requested_max_m: float
    satisfied: bool
    range_error_m: float | None
    facility: FacilityOut | None


class GenericRouteResponse(BaseModel):
    geometry: tuple[RoutePoint, ...]
    shape: Literal["round", "out_and_back"]

    distance_m: float
    distance_error_m: float
    distance_constraint_satisfied: bool

    constraints_satisfied: bool
    requirements_satisfied_count: int
    requirements_total: int
    facility_results: list[FacilityResultOut]

    elevation_gain_m: float
    repeated_segment_ratio: float
    pedestrian_path_ratio: float
    sharp_turn_count: int
    u_turn_count: int
    compactness: float
    quality_score: float


def _restroom_loader() -> list[Restroom]:
    """Lazily fetch Supabase restrooms -- only ever called when a
    restroom requirement is actually present (see
    `load_facility_catalog`), unlike the legacy endpoint's unconditional
    FastAPI dependency."""
    try:
        client = get_supabase_client()
        return fetch_eligible_restrooms(client)
    except Exception as exc:
        logger.exception(
            "Failed to fetch restroom data from Supabase for /routes; "
            "returning 503 to the client"
        )
        raise HTTPException(
            status_code=503,
            detail="Restroom data is temporarily unavailable",
        ) from exc


def _water_loader() -> list[Fountain]:
    return get_fountains()


def _facility_result_out(result: RequirementResult) -> FacilityResultOut:
    facility_out: FacilityOut | None = None
    if result.encounter is not None:
        encounter = result.encounter
        facility_out = FacilityOut(
            id=encounter.facility.id,
            kind=encounter.facility.kind,
            name=encounter.facility.name,
            status=encounter.facility.status,
            hours_of_operation=encounter.facility.hours_of_operation,
            latitude=encounter.facility.lat,
            longitude=encounter.facility.lon,
            mile_marker_m=encounter.mile_marker_m,
            off_route_distance_m=encounter.distance_to_route_m,
            encounter_index=encounter.encounter_index,
        )

    return FacilityResultOut(
        requirement_id=result.requirement.id,
        kind=result.requirement.kind,
        requested_min_m=result.requirement.min_distance_m,
        requested_max_m=result.requirement.max_distance_m,
        satisfied=result.satisfied,
        range_error_m=result.range_error_m,
        facility=facility_out,
    )


def _generic_response(
    scored: ScoredRoute,
    original_order: dict[str, int],
) -> GenericRouteResponse:
    route = scored.route
    candidate = route.candidate
    quality = route.quality
    geometry = candidate.geometry

    shape = route.shape
    assert shape in ("round", "out_and_back")

    facility_results = [
        _facility_result_out(result)
        for result in scored.facility_score.requirement_results
    ]
    facility_results.sort(
        key=lambda result: original_order.get(result.requirement_id, 0)
    )

    return GenericRouteResponse(
        geometry=geometry,
        shape=shape,
        distance_m=candidate.distance_m,
        distance_error_m=scored.distance_error_m,
        distance_constraint_satisfied=scored.distance_error_m <= 100.0,
        constraints_satisfied=scored.fully_valid,
        requirements_satisfied_count=scored.facility_score.requirements_satisfied_count,
        requirements_total=scored.facility_score.requirements_total,
        facility_results=facility_results,
        elevation_gain_m=quality.elevation_gain_m or 0.0,
        repeated_segment_ratio=quality.edge_reuse_ratio,
        pedestrian_path_ratio=quality.pedestrian_share,
        sharp_turn_count=sharp_turn_count(geometry),
        u_turn_count=u_turn_count(geometry),
        compactness=compactness(geometry),
        quality_score=scored.quality_score,
    )


@app.post(
    "/routes",
    dependencies=[Depends(rate_limit_dependency)],
)
def get_routes(
    request: RouteRequest,
    response: Response,
) -> list[GenericRouteResponse]:
    """Generic multi-facility route generation. Always uses the local
    engine (never ORS -- see module docstring above); an unavailable
    local graph is an honest 503, never a silent ORS fallback."""
    try:
        graph = get_graph()
    except Exception as exc:
        logger.exception("Local graph failed to load for /routes")
        raise HTTPException(
            status_code=503,
            detail="Routing engine is temporarily unavailable",
        ) from exc

    requirements = [
        FacilityRequirement(
            id=requirement.id,
            kind=requirement.kind,
            min_distance_m=requirement.min_distance_m,
            max_distance_m=requirement.max_distance_m,
        )
        for requirement in request.facility_requirements
    ]
    original_order = {
        requirement.id: index for index, requirement in enumerate(requirements)
    }
    run_time = request.run_time or datetime.now()

    # Raises 503 via _restroom_loader only when a restroom requirement
    # is actually present -- water-only/no-facility requests never touch
    # Supabase (see load_facility_catalog's conditional loading).
    facilities = load_facility_catalog(
        requirements,
        restroom_loader=_restroom_loader,
        water_loader=_water_loader,
        run_time=run_time,
    )

    start = Coordinate(lat=request.start_lat, lon=request.start_lon)

    try:
        scored = plan_routes(
            graph,
            start,
            request.target_distance_m,
            request.shape,
            request.count,
            requirements,
            facilities,
            constrained_planners=DEFAULT_CONSTRAINED_PLANNERS,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Local route generation failed for /routes")
        raise HTTPException(
            status_code=502,
            detail="Route generation failed unexpectedly",
        ) from exc

    if not scored:
        raise HTTPException(
            status_code=422,
            detail="No candidate route could be constructed for this request",
            headers={"X-Route-Engine": "local"},
        )

    response.headers["X-Route-Engine"] = "local"
    return [_generic_response(s, original_order) for s in scored]
