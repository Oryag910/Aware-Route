from collections.abc import Callable
from dataclasses import dataclass

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.geometry import bearing_deg, destination_point, haversine_m
from app.routing.parallel import run_concurrently
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
# Kept equal to MAX_REPAIR_ROUNDS: the 2026-07-17 benchmark showed
# spreading the shared budget across more near-misses converts more
# scenarios than letting two candidates go deep.
MAX_REPAIR_CALLS_PER_CANDIDATE = 3

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
        previous_attempt_error = float("inf")

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

            # Progress is judged against the previous attempt on this
            # anchor's trajectory, not against the original candidate:
            # converting a loop into an out-and-back passes through a
            # much-worse intermediate before the radial is resized
            # correctly, and judging that dip as failure aborts repair
            # one round before it converges (found live in the
            # 2026-07-17 benchmark regression).
            if attempt_error < previous_attempt_error:
                rounds_without_improvement = 0
            else:
                rounds_without_improvement += 1

            previous_attempt_error = attempt_error

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
        # Candidates within a pass are independent of each other (each
        # only reads/writes its own anchors), so they can be repaired
        # concurrently -- rounds stay sequential *within* a candidate
        # since each round depends on the previous response.
        nonlocal remaining_calls, provider_failed

        # Pre-assign budgets up front, most-promising-first (ascending
        # error), since concurrent calls can't dynamically claim from
        # a shared counter as they go. Any per-candidate budget left
        # unused (e.g. it converges early) is NOT redistributed to
        # later candidates in the pass -- that would require waiting
        # for earlier candidates to finish, defeating the parallelism.
        budgets: dict[int, int] = {}
        budget_remaining = remaining_calls

        for index in eligible:
            if budget_remaining <= 0:
                break

            assigned = min(
                MAX_REPAIR_CALLS_PER_CANDIDATE, budget_remaining
            )
            budgets[index] = assigned
            budget_remaining -= assigned

        if not budgets:
            return

        def make_task(index: int) -> Callable[[], _RepairOutcome]:
            return lambda: _repair_candidate(
                provider,
                targets[index],
                start,
                target_distance_m,
                max_calls=budgets[index],
            )

        tasks = [make_task(index) for index in budgets]
        outcomes = run_concurrently(tasks)

        calls_used_this_pass = 0
        any_provider_failure = False

        for index, outcome in zip(budgets, outcomes):
            if isinstance(outcome, Exception):
                # _repair_candidate only raises for bugs, not routing
                # failures (those are caught internally and reported
                # via _RepairOutcome.provider_failed) -- surface it.
                raise outcome

            calls_used_this_pass += outcome.calls_used
            repaired_indices.add(index)
            results[index] = outcome.best

            if outcome.provider_failed:
                any_provider_failure = True

        remaining_calls -= calls_used_this_pass
        provider_failed = any_provider_failure

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

    if (
        nothing_within_tolerance
        and not provider_failed
        and remaining_calls > 0
    ):
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
