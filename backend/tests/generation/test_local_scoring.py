from app.amenities.models import Amenity
from app.flow.interruptions import InterruptionStore
from app.generation.local_scoring import score_local_routes
from app.generation.routes import GeneratedRoute, QualityMetrics
from app.routing.provider import RouteCandidate, RoutePoint


EMPTY_STORE = InterruptionStore((), (), {}, {})


def _line(coords: list[tuple[float, float]]) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(lat=lat, lon=lon, elevation_m=0.0) for lat, lon in coords
    )


def _route(
    geometry: tuple[RoutePoint, ...],
    distance_m: float,
    *,
    shape: str = "round",
    iq: float = 0.3,
    pedestrian: float = 0.9,
    reuse: float = 0.0,
    elevation_gain_m: float | None = None,
    corrective: float = 0.0,
) -> GeneratedRoute:
    quality = QualityMetrics(
        edge_reuse_ratio=reuse,
        pedestrian_share=pedestrian,
        elevation_gain_m=elevation_gain_m,
        corrective_loop_penalty=corrective,
        isoperimetric_quotient=iq,
        waytype_breakdown={},
    )
    candidate = RouteCandidate(
        geometry=geometry,
        distance_m=distance_m,
        elevation_gain_m=0.0,
        extras=None,
    )
    return GeneratedRoute(
        candidate=candidate,
        node_path=[0, 1, 2],
        shape=shape,  # type: ignore[arg-type]
        quality=quality,
    )


# A geometry that passes an amenity, so a route can satisfy the amenity
# hard constraint. Amenity sits on the middle vertex. `matched` is a
# restroom-specific contract (this ranker only backs
# /routes/with-restroom), so these default fixtures are restroom-kind --
# see test_fountain_does_not_satisfy_restroom_match below for the
# fountain-specific contract tests.
_GEO_A = _line([(40.70, -74.00), (40.71, -74.00), (40.72, -74.00)])
_GEO_B = _line([(40.70, -73.95), (40.71, -73.95), (40.72, -73.95)])
_AMENITY_A = Amenity(lat=40.71, lon=-74.00, kind="restroom", name=None)
_AMENITY_B = Amenity(lat=40.71, lon=-73.95, kind="restroom", name=None)
_WIDE_RANGE = (0.0, 100_000.0)  # any matched amenity is "in range"


def test_hard_constraint_gates_matched() -> None:
    good = _route(_GEO_A, 5000.0)  # distance error 0, amenity in range
    too_long = _route(_GEO_B, 5000.0 + 400.0)  # distance error 400 > 100

    scored = score_local_routes(
        [too_long, good],
        [_AMENITY_A, _AMENITY_B],
        target_distance_m=5000.0,
        min_range_m=_WIDE_RANGE[0],
        max_range_m=_WIDE_RANGE[1],
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    by_geo = {id(s.route): s for s in scored}
    assert by_geo[id(good)].matched is True
    assert by_geo[id(too_long)].matched is False
    # Matched always ranks ahead of a hard-constraint failure.
    assert scored[0].route is good


def test_no_amenity_in_range_is_not_matched() -> None:
    # Amenity exists but nowhere near the route -> not matched.
    lonely = Amenity(lat=41.5, lon=-74.5, kind="fountain", name=None)
    route = _route(_GEO_A, 5000.0)

    scored = score_local_routes(
        [route],
        [lonely],
        target_distance_m=5000.0,
        min_range_m=0.0,
        max_range_m=100_000.0,
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    assert scored[0].matched is False
    assert scored[0].best_amenity is None


def test_shape_round_prefers_high_roundness() -> None:
    round_like = _route(_GEO_A, 5000.0, iq=0.8)
    linear_like = _route(_GEO_B, 5000.0, iq=0.1)

    scored = score_local_routes(
        [linear_like, round_like],
        [_AMENITY_A, _AMENITY_B],
        target_distance_m=5000.0,
        min_range_m=0.0,
        max_range_m=100_000.0,
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    assert scored[0].route is round_like


def test_shape_out_and_back_prefers_low_roundness() -> None:
    round_like = _route(_GEO_A, 5000.0, iq=0.8, shape="out_and_back")
    linear_like = _route(_GEO_B, 5000.0, iq=0.1, shape="out_and_back")

    scored = score_local_routes(
        [round_like, linear_like],
        [_AMENITY_A, _AMENITY_B],
        target_distance_m=5000.0,
        min_range_m=0.0,
        max_range_m=100_000.0,
        elevation_preference="flat",
        shape="out_and_back",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    assert scored[0].route is linear_like


def test_elevation_none_is_neutral() -> None:
    route = _route(_GEO_A, 5000.0, elevation_gain_m=None)

    scored = score_local_routes(
        [route],
        [_AMENITY_A],
        target_distance_m=5000.0,
        min_range_m=0.0,
        max_range_m=100_000.0,
        elevation_preference="hilly",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    assert scored[0].elevation_penalty == 0.0
    assert scored[0].elevation_bucket is None


def test_interpretable_fields_populated() -> None:
    route = _route(_GEO_A, 5000.0, pedestrian=0.6)

    scored = score_local_routes(
        [route],
        [_AMENITY_A],
        target_distance_m=5000.0,
        min_range_m=0.0,
        max_range_m=100_000.0,
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )[0]

    assert scored.surface_penalty == 0.4  # 1 - pedestrian_share
    assert scored.distance_error_m == 0.0
    assert scored.best_amenity is not None
    assert 0.0 <= scored.composite_score <= 1.0


def test_fountain_does_not_satisfy_restroom_match() -> None:
    """A fountain in range must never make /routes/with-restroom's
    `matched` true -- only a restroom satisfies the restroom
    mile-range constraint."""
    fountain = Amenity(lat=40.71, lon=-74.00, kind="fountain", name=None)
    route = _route(_GEO_A, 5000.0)  # distance error 0, fountain in range

    scored = score_local_routes(
        [route],
        [fountain],
        target_distance_m=5000.0,
        min_range_m=_WIDE_RANGE[0],
        max_range_m=_WIDE_RANGE[1],
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    assert scored[0].matched is False
    # Still surfaced for display ("closest available"), but unambiguously
    # a fountain, not a restroom standing in for one.
    assert scored[0].best_amenity is not None
    assert scored[0].best_amenity.amenity.kind == "fountain"


def test_restroom_preferred_over_closer_fountain() -> None:
    """When both a restroom and a fountain are on-route, but only the
    fountain actually falls inside the requested mile range, the route
    must still be scored against the restroom -- a well-placed fountain
    must never flip `matched` to True on the restroom's behalf, even
    though (pre-fix) picking the amenity pool's best-fit candidate
    regardless of kind would have done exactly that."""
    geometry = _line(
        [
            (40.70, -74.00),
            (40.71, -74.00),  # restroom here, mile marker ~1112m
            (40.72, -74.00),  # fountain here, mile marker ~2224m
            (40.73, -74.00),
        ]
    )
    restroom = Amenity(lat=40.71, lon=-74.00, kind="restroom", name=None)
    fountain = Amenity(lat=40.72, lon=-74.00, kind="fountain", name=None)
    route = _route(geometry, 5000.0)

    # Requested band brackets the fountain (mile ~2224m) but sits ~888m
    # beyond the restroom (mile ~1112m) -- well outside the 500m hard
    # constraint tolerance, so only the fountain "fits" by distance alone.
    min_range_m = 2000.0
    max_range_m = 2500.0

    scored = score_local_routes(
        [route],
        [restroom, fountain],
        target_distance_m=5000.0,
        min_range_m=min_range_m,
        max_range_m=max_range_m,
        elevation_preference="flat",
        shape="round",
        interruption_store=EMPTY_STORE,
        count=5,
    )

    # The fountain fits the band; the restroom doesn't -- so the route
    # must be unmatched, not matched via the fountain.
    assert scored[0].matched is False
    assert scored[0].best_amenity is not None
    assert scored[0].best_amenity.amenity.kind == "restroom"
