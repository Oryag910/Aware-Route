from dataclasses import dataclass
from statistics import median

from app.routing.geometry import haversine_m
from app.routing.provider import Coordinate, RoutePoint


# ORS foot-walking geometry vertices are spaced ~19m apart, so a 5-point
# window covers ~100m of route -- wide enough to flatten DEM jitter
# (single-vertex elevation spikes) without also flattening a real climb,
# which unfolds over hundreds of meters.
SMOOTHING_WINDOW = 5

# A climb must average at least this grade...
MIN_CLIMB_GRADE_PCT = 2.0

# ...sustained over at least this much distance to count -- filters out
# short steep bumps (a single driveway ramp, a curb cut) that aren't a
# meaningful part of a run's difficulty.
MIN_CLIMB_LENGTH_M = 200.0

# A climb tolerates a brief non-climbing dip (e.g. a short flat/downhill
# stretch between two humps of the same hill) of up to this much
# distance before it's considered ended. Bigger dips split the climb.
CLIMB_DIP_TOLERANCE_M = 60.0

# Window used to find the single steepest stretch of the smoothed
# profile, for max_grade_pct.
GRADE_WINDOW_M = 50.0


def smooth_elevations(geometry: tuple[RoutePoint, ...]) -> list[float]:
    """Rolling median of elevation_m over a centered SMOOTHING_WINDOW,
    clamped at the ends (the window shrinks rather than wrapping or
    padding). A rolling median (rather than mean) is what actually kills
    single-vertex DEM spikes -- a mean would still drag the profile
    toward the noisy point, a median ignores it outright as long as it's
    a minority within the window."""
    if len(geometry) <= 2:
        return [point.elevation_m for point in geometry]

    half_window = SMOOTHING_WINDOW // 2
    smoothed: list[float] = []

    for index in range(len(geometry)):
        window_start = max(0, index - half_window)
        window_end = min(len(geometry), index + half_window + 1)

        window_values = [
            point.elevation_m
            for point in geometry[window_start:window_end]
        ]

        smoothed.append(median(window_values))

    return smoothed


def smoothed_gain_m(geometry: tuple[RoutePoint, ...]) -> float:
    """Sum of positive deltas over the smoothed elevation profile --
    the trustworthy replacement for summing raw ORS ascent, which
    includes DEM jitter (a real flat 8km Midtown loop once reported
    342m of "ascent" this way)."""
    smoothed = smooth_elevations(geometry)

    return sum(
        max(0.0, later - earlier)
        for earlier, later in zip(smoothed, smoothed[1:])
    )


def _cumulative_distances_m(
    geometry: tuple[RoutePoint, ...],
) -> list[float]:
    cumulative = [0.0]

    for index in range(len(geometry) - 1):
        segment_m = haversine_m(
            Coordinate(lat=geometry[index].lat, lon=geometry[index].lon),
            Coordinate(
                lat=geometry[index + 1].lat,
                lon=geometry[index + 1].lon,
            ),
        )
        cumulative.append(cumulative[-1] + segment_m)

    return cumulative


@dataclass(frozen=True)
class Climb:
    start_index: int
    end_index: int
    length_m: float
    gain_m: float
    avg_grade_pct: float


def detect_climbs(geometry: tuple[RoutePoint, ...]) -> list[Climb]:
    """Walk the smoothed profile vertex by vertex, tracking a running
    "high point" of the current climb attempt. Distance where the
    profile is at or below that high point is a dip; once accumulated
    dip distance exceeds CLIMB_DIP_TOLERANCE_M the climb is closed off
    at the high point (trimming the trailing dip from its extent) and
    kept only if its overall average grade clears MIN_CLIMB_GRADE_PCT
    over at least MIN_CLIMB_LENGTH_M. A new high point resets the dip
    counter, so brief dips within tolerance don't end the climb --
    but bigger dips do, and nothing is merged across them."""
    if len(geometry) < 2:
        return []

    smoothed = smooth_elevations(geometry)
    cumulative_m = _cumulative_distances_m(geometry)

    climbs: list[Climb] = []

    climb_start: int | None = None
    high_index: int | None = None

    def close_climb(end_index: int) -> None:
        nonlocal climb_start, high_index

        if climb_start is not None and end_index > climb_start:
            length_m = cumulative_m[end_index] - cumulative_m[climb_start]
            gain_m = smoothed[end_index] - smoothed[climb_start]

            if (
                length_m >= MIN_CLIMB_LENGTH_M
                and gain_m > 0
                and (gain_m / length_m) * 100.0 >= MIN_CLIMB_GRADE_PCT
            ):
                climbs.append(
                    Climb(
                        start_index=climb_start,
                        end_index=end_index,
                        length_m=length_m,
                        gain_m=gain_m,
                        avg_grade_pct=(gain_m / length_m) * 100.0,
                    )
                )

        climb_start = None
        high_index = None

    for index in range(1, len(geometry)):
        if climb_start is None:
            # A climb can only start on an uphill step.
            if smoothed[index] > smoothed[index - 1]:
                climb_start = index - 1
                high_index = index - 1

            # Fall through so this same step is also evaluated as a
            # potential first leg of the newly-started climb below.
            if climb_start is None:
                continue

        assert high_index is not None

        if smoothed[index] > smoothed[high_index]:
            high_index = index
            continue

        # At or below the running high point -- this is dip distance.
        dip_m = cumulative_m[index] - cumulative_m[high_index]

        if dip_m > CLIMB_DIP_TOLERANCE_M:
            close_climb(high_index)

            # Re-evaluate this vertex as a possible new climb start,
            # since the dip that just ended the old climb may itself
            # be followed immediately by fresh uphill.
            if smoothed[index] > smoothed[index - 1]:
                climb_start = index - 1
                high_index = index - 1

    if climb_start is not None and high_index is not None:
        close_climb(high_index)

    return climbs


def max_grade_pct(geometry: tuple[RoutePoint, ...]) -> float:
    """Steepest average grade over any GRADE_WINDOW_M stretch of the
    smoothed profile, scanning from every vertex forward to the first
    point at least GRADE_WINDOW_M away."""
    if len(geometry) < 2:
        return 0.0

    smoothed = smooth_elevations(geometry)
    cumulative_m = _cumulative_distances_m(geometry)

    total_distance_m = cumulative_m[-1]

    if total_distance_m < GRADE_WINDOW_M:
        return 0.0

    steepest = 0.0
    window_end = 0

    for start in range(len(geometry) - 1):
        target_m = cumulative_m[start] + GRADE_WINDOW_M

        if target_m > total_distance_m:
            break

        window_end = max(window_end, start + 1)

        while cumulative_m[window_end] < target_m:
            window_end += 1

        length_m = cumulative_m[window_end] - cumulative_m[start]

        if length_m <= 0:
            continue

        grade_pct = (
            (smoothed[window_end] - smoothed[start]) / length_m
        ) * 100.0

        steepest = max(steepest, grade_pct)

    return steepest
