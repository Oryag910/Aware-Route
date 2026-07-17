import pytest

from app.flow.elevation import (
    CLIMB_DIP_TOLERANCE_M,
    GRADE_WINDOW_M,
    MIN_CLIMB_LENGTH_M,
    detect_climbs,
    max_grade_pct,
    smooth_elevations,
    smoothed_gain_m,
)
from app.routing.provider import RoutePoint


LAT_STEP_DEG = 0.0002  # ~22m per step at this latitude


def make_geometry(
    elevations: list[float],
    lat_step: float = LAT_STEP_DEG,
    lon: float = -74.00,
) -> tuple[RoutePoint, ...]:
    return tuple(
        RoutePoint(
            lat=40.70 + lat_step * index,
            lon=lon,
            elevation_m=elevation,
        )
        for index, elevation in enumerate(elevations)
    )


def test_smooth_elevations_degenerate_input_returns_raw() -> None:
    geometry = make_geometry([0.0])
    assert smooth_elevations(geometry) == [0.0]

    geometry_two = make_geometry([0.0, 5.0])
    assert smooth_elevations(geometry_two) == [0.0, 5.0]


def test_smoothed_gain_m_flat_profile_ignores_single_point_spikes() -> None:  # noqa: E501
    # A flat route (0.0 everywhere) with isolated single-vertex DEM
    # spikes -- mimics the 342m-Midtown-loop scenario in miniature.
    # Each spike is surrounded by enough flat neighbors that the
    # 5-point rolling median throws it out entirely.
    elevations = [0.0] * 60
    spike_indices = [10, 25, 40, 55]

    for index in spike_indices:
        elevations[index] = 40.0

    geometry = make_geometry(elevations)

    raw_positive_sum = sum(
        max(0.0, later - earlier)
        for earlier, later in zip(elevations, elevations[1:])
    )
    assert raw_positive_sum > 100.0  # noisy raw signal is large

    result = smoothed_gain_m(geometry)
    assert result == pytest.approx(0.0)


def test_smoothed_gain_m_clean_ramp() -> None:
    # A steady, monotonic ramp -- smoothing shouldn't remove much real
    # gain (the clamped window at each end softens the first/last
    # couple of deltas slightly, hence the wider tolerance).
    elevations = [float(index) for index in range(20)]
    geometry = make_geometry(elevations)

    result = smoothed_gain_m(geometry)

    assert result == pytest.approx(19.0, abs=3.0)


def _climb_geometry(
    flat_before: int,
    climb_points: int,
    grade_pct: float,
    flat_after: int,
    lat_step: float = LAT_STEP_DEG,
) -> tuple[RoutePoint, ...]:
    """A flat run-up, a sustained climb at grade_pct (using lat_step's
    real ground distance per point), then a flat run-out."""
    step_m = lat_step * 111_320.0  # approx meters per degree latitude
    rise_per_point = step_m * (grade_pct / 100.0)

    elevations: list[float] = [0.0] * flat_before

    for index in range(climb_points):
        elevations.append(
            elevations[-1] + rise_per_point if elevations else rise_per_point
        )

    elevations.extend([elevations[-1]] * flat_after)

    return make_geometry(elevations, lat_step=lat_step)


def test_detect_climbs_single_sustained_climb() -> None:
    # 30 points at ~22m spacing and 5% grade -> length ~30*22=660m,
    # comfortably over MIN_CLIMB_LENGTH_M and MIN_CLIMB_GRADE_PCT.
    geometry = _climb_geometry(
        flat_before=5, climb_points=30, grade_pct=5.0, flat_after=5
    )

    climbs = detect_climbs(geometry)

    assert len(climbs) == 1
    climb = climbs[0]

    assert climb.length_m >= MIN_CLIMB_LENGTH_M
    assert climb.gain_m > 0.0
    assert climb.avg_grade_pct == pytest.approx(5.0, abs=0.5)
    assert climb.start_index <= 5
    assert climb.end_index >= 30


def test_detect_climbs_tolerates_brief_dip() -> None:
    # Two climbing legs separated by a short flat dip well under
    # CLIMB_DIP_TOLERANCE_M -- should merge into a single climb.
    step_m = LAT_STEP_DEG * 111_320.0
    rise_per_point = step_m * (5.0 / 100.0)

    elevations = [0.0] * 5

    for _ in range(15):
        elevations.append(elevations[-1] + rise_per_point)

    # Dip: 2 flat points (~2*22=44m < 60m tolerance).
    dip_points = 2
    assert dip_points * step_m < CLIMB_DIP_TOLERANCE_M
    elevations.extend([elevations[-1]] * dip_points)

    for _ in range(15):
        elevations.append(elevations[-1] + rise_per_point)

    elevations.extend([elevations[-1]] * 5)

    geometry = make_geometry(elevations)

    climbs = detect_climbs(geometry)

    assert len(climbs) == 1
    assert climbs[0].length_m >= MIN_CLIMB_LENGTH_M


def test_detect_climbs_long_dip_splits_climbs() -> None:
    # Same shape as above, but the dip is long enough (well over
    # CLIMB_DIP_TOLERANCE_M) to end the first climb and start a second.
    step_m = LAT_STEP_DEG * 111_320.0
    rise_per_point = step_m * (5.0 / 100.0)

    elevations = [0.0] * 5

    for _ in range(15):
        elevations.append(elevations[-1] + rise_per_point)

    # Dip: 10 flat points (~10*22=220m > 60m tolerance).
    dip_points = 10
    assert dip_points * step_m > CLIMB_DIP_TOLERANCE_M
    elevations.extend([elevations[-1]] * dip_points)

    for _ in range(15):
        elevations.append(elevations[-1] + rise_per_point)

    elevations.extend([elevations[-1]] * 5)

    geometry = make_geometry(elevations)

    climbs = detect_climbs(geometry)

    assert len(climbs) == 2

    for climb in climbs:
        assert climb.length_m >= MIN_CLIMB_LENGTH_M


def test_detect_climbs_short_steep_bump_not_a_climb() -> None:
    # Steep (10%) but short -- well under MIN_CLIMB_LENGTH_M -- should
    # not register as a climb even though the grade threshold is met.
    step_m = LAT_STEP_DEG * 111_320.0
    rise_per_point = step_m * (10.0 / 100.0)

    elevations = [0.0] * 5

    bump_points = 5  # ~5*22=110m << 200m
    assert bump_points * step_m < MIN_CLIMB_LENGTH_M

    for _ in range(bump_points):
        elevations.append(elevations[-1] + rise_per_point)

    elevations.extend([elevations[-1]] * 10)

    geometry = make_geometry(elevations)

    climbs = detect_climbs(geometry)

    assert climbs == []


def test_detect_climbs_gentle_grade_not_a_climb() -> None:
    # Long stretch but grade well under MIN_CLIMB_GRADE_PCT.
    geometry = _climb_geometry(
        flat_before=5, climb_points=30, grade_pct=0.5, flat_after=5
    )

    climbs = detect_climbs(geometry)

    assert climbs == []


def test_detect_climbs_degenerate_input() -> None:
    assert detect_climbs(make_geometry([])) == []
    assert detect_climbs(make_geometry([5.0])) == []


def test_max_grade_pct_sanity() -> None:
    flat_geometry = _climb_geometry(
        flat_before=10, climb_points=0, grade_pct=0.0, flat_after=10
    )
    assert max_grade_pct(flat_geometry) == pytest.approx(0.0)

    steep_geometry = _climb_geometry(
        flat_before=5, climb_points=20, grade_pct=8.0, flat_after=5
    )
    result = max_grade_pct(steep_geometry)
    assert result == pytest.approx(8.0, abs=1.0)


def test_max_grade_pct_degenerate_input() -> None:
    assert max_grade_pct(make_geometry([])) == 0.0
    assert max_grade_pct(make_geometry([5.0])) == 0.0


def test_max_grade_pct_short_route_below_window() -> None:
    # Total route distance shorter than GRADE_WINDOW_M -- no window
    # fits, so the result is the degenerate 0.0 rather than a spurious
    # reading over a too-short stretch.
    geometry = make_geometry([0.0, 10.0], lat_step=0.0001)
    total_m = 0.0001 * 111_320.0
    assert total_m < GRADE_WINDOW_M

    assert max_grade_pct(geometry) == pytest.approx(0.0)
