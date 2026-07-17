from dataclasses import dataclass

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.geometry import bearing_deg, destination_point, haversine_m
from app.routing.provider import (
    Coordinate,
    RouteCandidate,
    RoutePoint,
    RoutingProvider,
)


# A candidate within this many meters of the target is already accurate
# enough — matches the hard constraint used downstream in scoring.py.
MAX_DISTANCE_ERROR_M = 100.0

# Candidates within this fraction of the target get repaired in the
# first pass — close enough that a nudged waypoint plausibly fixes them.
NEAR_MISS_RATIO = 0.15

# When the first pass leaves no candidate within tolerance and budget
# remains, a second "rescue" pass widens eligibility up to this fraction
# — covers areas like Battery Park where every candidate misses badly
# and nothing would otherwise be repaired at all.
RESCUE_RATIO = 0.5

# Bound on corrective rounds per anchor for a single candidate.
MAX_REPAIR_ROUNDS = 3

# Bound on total ORS calls one candidate can consume across all its
# anchors, so a stubborn candidate can't eat the whole request budget.
MAX_REPAIR_CALLS_PER_CANDIDATE = 4

# Shared budget across all repaired candidates in one request, so a
# request with many near-misses can't blow through the ORS rate limit.
MAX_REPAIR_CALLS_PER_REQUEST = 8

# Nudging the anchor 1m outward typically adds ~2m of route (out and
# back). Used for the first step; later steps use the rate actually
# measured from the previous attempt, clamped to a sane band so one
# odd response can't produce a wild next step.
ASSUMED_ROUTE_M_PER_NUDGE_M = 2.0
MIN_ROUTE_M_PER_NUDGE_M = 0.5
MAX_ROUTE_M_PER_NUDGE_M = 4.0

# Fallback anchors at these fractions of the route's cumulative distance
# give repair a second chance when the furthest point is unroutable or
# unproductive (e.g. nudging a waterfront turnaround into the river).
FALLBACK_ANCHOR_FRACTIONS = (0.35, 0.65)
MIN_ANCHOR_SEPARATION_M = 50.0

# Give up on an anchor after this many consecutive non-improving
# attempts and move to the next one.
MAX_ROUNDS_WITHOUT_IMPROVEMENT = 2


@dataclass(frozen=True)
class RepairTarget:
    """A candidate plus the restroom waypoint (if any) its route must
    keep passing through while repair reshapes it — without this,
    fixing a restroom-first candidate's distance would silently drop
    the restroom the route was built around."""

    candidate: RouteCandidate
    via: Coordinate | None = None


@dataclass(frozen=True)
class _RepairOutcome:
    best: RouteCandidate
    calls_used: int
    provider_failed: bool


def _anchor_points(
    geometry: tuple[RoutePoint, ...],
    start: Coordinate,
) -> list[Coordinate]:
    points = [
        Coordinate(lat=point.lat, lon=point.lon) for point in geometry
    ]

    anchors = [max(points, key=lambda point: haversine_m(start, point))]

    cumulative_distances = [0.0]
    for previous, current in zip(points, points[1:]):
        cumulative_distances.append(
            cumulative_distances[-1] + haversine_m(previous, current)
        )

    total = cumulative_distances[-1]

    for fraction in FALLBACK_ANCHOR_FRACTIONS:
        threshold = fraction * total
        index = next(
            i
            for i, distance in enumerate(cumulative_distances)
            if distance >= threshold
        )
        fallback = points[index]

        if all(
            haversine_m(fallback, existing) >= MIN_ANCHOR_SEPARATION_M
            for existing in anchors
        ):
            anchors.append(fallback)

    return anchors


def _repair_candidate(
    provider: RoutingProvider,
    target: RepairTarget,
    start: Coordinate,
    target_distance_m: float,
    max_calls: int,
) -> _RepairOutcome:
    best = target.candidate
    best_error = abs(best.distance_m - target_distance_m)
    calls_used = 0

    for anchor in _anchor_points(target.candidate.geometry, start):
        if calls_used >= max_calls:
            break

        bearing = bearing_deg(start, anchor)
        radial_m = haversine_m(start, anchor)
        previous_distance_m = target.candidate.distance_m
        nudge_m = (
            target_distance_m - previous_distance_m
        ) / ASSUMED_ROUTE_M_PER_NUDGE_M
        rounds_without_improvement = 0

        for _ in range(MAX_REPAIR_ROUNDS):
            if calls_used >= max_calls or abs(nudge_m) < 1.0:
                break

            radial_m += nudge_m

            if radial_m <= 0:
                break

            new_anchor = destination_point(start, bearing, radial_m)
            waypoints = (
                [start, target.via, new_anchor, start]
                if target.via is not None
                else [start, new_anchor, start]
            )

            try:
                attempt = provider.get_route_through_waypoints(waypoints)
            except RouteNotFoundError:
                # This anchor is unroutable (still costs an ORS call)
                # — move to the next anchor rather than giving up on
                # the candidate.
                calls_used += 1
                break
            except RoutingProviderError:
                # Provider-level trouble (rate limit, outage): stop
                # spending repair calls for this whole request.
                return _RepairOutcome(best, calls_used + 1, True)

            calls_used += 1
            attempt_error = abs(attempt.distance_m - target_distance_m)

            if attempt_error < best_error:
                best = attempt
                best_error = attempt_error
                rounds_without_improvement = 0
            else:
                rounds_without_improvement += 1

            if best_error <= MAX_DISTANCE_ERROR_M:
                return _RepairOutcome(best, calls_used, False)

            if rounds_without_improvement >= MAX_ROUNDS_WITHOUT_IMPROVEMENT:
                break

            measured_rate = (
                attempt.distance_m - previous_distance_m
            ) / nudge_m
            rate = (
                measured_rate
                if MIN_ROUTE_M_PER_NUDGE_M
                <= measured_rate
                <= MAX_ROUTE_M_PER_NUDGE_M
                else ASSUMED_ROUTE_M_PER_NUDGE_M
            )

            nudge_m = (target_distance_m - attempt.distance_m) / rate
            previous_distance_m = attempt.distance_m

    return _RepairOutcome(best, calls_used, False)


def repair_near_miss_candidates(
    provider: RoutingProvider,
    targets: list[RepairTarget],
    start: Coordinate,
    target_distance_m: float,
) -> list[RouteCandidate]:
    results = [target.candidate for target in targets]

    def error_m(candidate: RouteCandidate) -> float:
        return abs(candidate.distance_m - target_distance_m)

    remaining_calls = MAX_REPAIR_CALLS_PER_REQUEST
    repaired_indices: set[int] = set()
    provider_failed = False

    def run_pass(eligible: list[int]) -> None:
        nonlocal remaining_calls, provider_failed

        for index in eligible:
            if remaining_calls <= 0 or provider_failed:
                return

            outcome = _repair_candidate(
                provider,
                targets[index],
                start,
                target_distance_m,
                max_calls=min(
                    MAX_REPAIR_CALLS_PER_CANDIDATE, remaining_calls
                ),
            )
            remaining_calls -= outcome.calls_used
            repaired_indices.add(index)
            results[index] = outcome.best
            provider_failed = outcome.provider_failed

    # First pass: near-misses, most promising (smallest error) first,
    # so the shared budget goes to the candidates likeliest to convert.
    near_miss_indices = sorted(
        (
            index
            for index, target in enumerate(targets)
            if MAX_DISTANCE_ERROR_M
            < error_m(target.candidate)
            <= NEAR_MISS_RATIO * target_distance_m
        ),
        key=lambda index: error_m(targets[index].candidate),
    )
    run_pass(near_miss_indices)

    nothing_within_tolerance = all(
        error_m(candidate) > MAX_DISTANCE_ERROR_M for candidate in results
    )

    if nothing_within_tolerance and not provider_failed:
        rescue_indices = sorted(
            (
                index
                for index, target in enumerate(targets)
                if index not in repaired_indices
                and NEAR_MISS_RATIO * target_distance_m
                < error_m(target.candidate)
                <= RESCUE_RATIO * target_distance_m
            ),
            key=lambda index: error_m(targets[index].candidate),
        )
        run_pass(rescue_indices)

    return results
