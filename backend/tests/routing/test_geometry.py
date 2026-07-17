import pytest

from app.routing.geometry import bearing_deg, destination_point, haversine_m
from app.routing.provider import Coordinate


def test_haversine_m_one_degree_latitude() -> None:
    # 1 degree of latitude is ~111.19km everywhere on the sphere,
    # independent of longitude -- a simple known-value sanity check.
    origin = Coordinate(lat=0.0, lon=0.0)
    destination = Coordinate(lat=1.0, lon=0.0)

    distance = haversine_m(origin, destination)

    assert distance == pytest.approx(111_194.93, rel=1e-3)


def test_haversine_m_same_point_is_zero() -> None:
    point = Coordinate(lat=40.7128, lon=-74.0060)

    assert haversine_m(point, point) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    ("destination", "expected_bearing"),
    [
        (Coordinate(lat=1.0, lon=0.0), 0.0),
        (Coordinate(lat=0.0, lon=1.0), 90.0),
        (Coordinate(lat=-1.0, lon=0.0), 180.0),
        (Coordinate(lat=0.0, lon=-1.0), 270.0),
    ],
)
def test_bearing_deg_cardinal_directions(
    destination: Coordinate,
    expected_bearing: float,
) -> None:
    origin = Coordinate(lat=0.0, lon=0.0)

    bearing = bearing_deg(origin, destination)

    assert bearing == pytest.approx(expected_bearing, abs=0.5)


def test_destination_point_round_trips_with_haversine_and_bearing() -> None:
    origin = Coordinate(lat=40.7128, lon=-74.0060)
    bearing = 45.0
    distance_m = 1000.0

    destination = destination_point(origin, bearing, distance_m)

    assert haversine_m(origin, destination) == pytest.approx(
        distance_m,
        rel=1e-3,
    )
    assert bearing_deg(origin, destination) == pytest.approx(
        bearing,
        abs=0.5,
    )


def test_destination_point_zero_distance_returns_origin() -> None:
    origin = Coordinate(lat=40.7128, lon=-74.0060)

    destination = destination_point(origin, bearing=90.0, distance_m=0.0)

    assert destination.lat == pytest.approx(origin.lat)
    assert destination.lon == pytest.approx(origin.lon)
