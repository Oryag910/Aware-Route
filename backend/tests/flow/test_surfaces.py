import pytest

from app.flow.surfaces import contains_stairs, pedestrian_path_ratio
from app.routing.provider import RouteExtras, RoutePoint


def make_geometry(count: int) -> tuple[RoutePoint, ...]:
    # Evenly spaced points along a meridian -- consecutive points are
    # roughly equidistant, so waytype-segment coverage translates
    # cleanly into a predictable distance fraction.
    return tuple(
        RoutePoint(
            lat=40.70 + 0.001 * index,
            lon=-74.00,
            elevation_m=0.0,
        )
        for index in range(count)
    )


def test_pedestrian_path_ratio_none_extras_is_zero() -> None:
    geometry = make_geometry(4)

    assert pedestrian_path_ratio(geometry, None) == pytest.approx(0.0)


def test_pedestrian_path_ratio_short_geometry_is_zero() -> None:
    geometry = make_geometry(1)
    extras = RouteExtras(
        waytype_segments=((0, 1, 7),),
        surface_segments=(),
        steepness_segments=(),
    )

    assert pedestrian_path_ratio(geometry, extras) == pytest.approx(0.0)


def test_pedestrian_path_ratio_full_footway_coverage() -> None:
    # 4 points -> 3 segments, all tagged footway (code 7).
    geometry = make_geometry(4)
    extras = RouteExtras(
        waytype_segments=((0, 3, 7),),
        surface_segments=(),
        steepness_segments=(),
    )

    assert pedestrian_path_ratio(geometry, extras) == pytest.approx(1.0)


def test_pedestrian_path_ratio_partial_coverage() -> None:
    # 4 evenly-spaced points -> 3 equal-length segments. Only the first
    # segment (0-1) is pedestrian (path, code 4); the remaining two
    # segments (road, code 2) are not.
    geometry = make_geometry(4)
    extras = RouteExtras(
        waytype_segments=((0, 1, 4), (1, 3, 2)),
        surface_segments=(),
        steepness_segments=(),
    )

    assert pedestrian_path_ratio(geometry, extras) == pytest.approx(
        1 / 3
    )


def test_pedestrian_path_ratio_non_pedestrian_waytype_is_zero() -> None:
    geometry = make_geometry(3)
    extras = RouteExtras(
        waytype_segments=((0, 2, 2),),  # road
        surface_segments=(),
        steepness_segments=(),
    )

    assert pedestrian_path_ratio(geometry, extras) == pytest.approx(0.0)


def test_contains_stairs_none_extras_is_false() -> None:
    geometry = make_geometry(3)

    assert contains_stairs(geometry, None) is False


def test_contains_stairs_true_when_steps_segment_present() -> None:
    geometry = make_geometry(3)
    extras = RouteExtras(
        waytype_segments=((0, 1, 2), (1, 2, 8)),  # steps
        surface_segments=(),
        steepness_segments=(),
    )

    assert contains_stairs(geometry, extras) is True


def test_contains_stairs_false_without_steps_segment() -> None:
    geometry = make_geometry(3)
    extras = RouteExtras(
        waytype_segments=((0, 2, 7),),  # footway
        surface_segments=(),
        steepness_segments=(),
    )

    assert contains_stairs(geometry, extras) is False
