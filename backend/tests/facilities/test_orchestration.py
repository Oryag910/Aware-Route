from unittest.mock import patch

from app.facilities.orchestration import natural_match_pool
from app.routing.provider import Coordinate


def test_no_requirements_requests_exactly_count_candidates() -> None:
    graph = object()
    with patch("app.facilities.orchestration.generate_routes") as mocked:
        mocked.return_value = []
        natural_match_pool(
            graph=graph,
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="mix",
            count=3,
            requirements=[],
        )
        mocked.assert_called_once_with(graph, Coordinate(lat=40.0, lon=-73.0), 8000.0, "mix", 3)


def test_with_requirements_requests_overcomplete_pool() -> None:
    from app.facilities.models import FacilityRequirement

    req = FacilityRequirement(id="r1", kind="restroom", min_distance_m=0, max_distance_m=1000)
    with patch("app.facilities.orchestration.generate_routes") as mocked:
        mocked.return_value = []
        natural_match_pool(
            graph=object(),
            start=Coordinate(lat=40.0, lon=-73.0),
            target_distance_m=8000.0,
            shape="mix",
            count=3,
            requirements=[req],
        )
        called_pool_size = mocked.call_args[0][4]
        assert called_pool_size > 3
