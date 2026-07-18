import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.ors import ORS_URL, OpenRouteServiceProvider
from app.routing.provider import Coordinate


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "routing"


def load_fixture(filename: str) -> dict[str, object]:
    fixture_path = FIXTURE_DIRECTORY / filename

    with fixture_path.open() as fixture_file:
        data: object = json.load(fixture_file)

    if not isinstance(data, dict):
        raise ValueError("Fixture must contain a JSON object")

    return cast(dict[str, object], data)


def make_response(
    status_code: int,
    body: dict[str, object],
) -> httpx.Response:
    request = httpx.Request("POST", ORS_URL)

    return httpx.Response(
        status_code=status_code,
        json=body,
        request=request,
    )


def test_get_loop_parses_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("round_trip_success.json")
    captured_body: dict[str, object] = {}

    def fake_post(
        *_args: object,
        **kwargs: object,
    ) -> httpx.Response:
        captured_body.update(cast(dict[str, object], kwargs["json"]))
        return make_response(200, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    candidate = provider.get_loop(
        start=Coordinate(lat=40.7128, lon=-74.0060),
        target_distance_m=5000.0,
        seed=1,
    )

    # The round trip must be generated with ferry avoidance, or ORS
    # will happily send the loop across the river on a ferry line.
    options = cast(dict[str, object], captured_body["options"])
    assert "round_trip" in options
    assert options["avoid_features"] == ["ferries"]

    features = cast(list[dict[str, object]], fixture["features"])
    feature = features[0]

    properties = cast(dict[str, object], feature["properties"])
    summary = cast(dict[str, object], properties["summary"])

    geometry_data = cast(dict[str, object], feature["geometry"])
    raw_coordinates = cast(
        list[list[int | float]],
        geometry_data["coordinates"],
    )

    assert candidate.distance_m == float(
        cast(int | float, summary["distance"])
    )
    assert candidate.elevation_gain_m == float(
        cast(int | float, properties["ascent"])
    )
    assert len(candidate.geometry) == len(raw_coordinates)

    assert candidate.geometry[0].lon == float(raw_coordinates[0][0])
    assert candidate.geometry[0].lat == float(raw_coordinates[0][1])
    assert candidate.geometry[0].elevation_m == float(
        raw_coordinates[0][2]
    )


def test_distance_limit_raises_route_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("error_400_distance_limit.json")

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(400, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    with pytest.raises(RouteNotFoundError) as error:
        provider.get_loop(
            start=Coordinate(lat=40.7128, lon=-74.0060),
            target_distance_m=110000.0,
            seed=1,
        )

    assert "requested route length" in str(error.value)


def test_missing_routable_point_raises_route_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("error_404_no_point.json")

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(404, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    with pytest.raises(RouteNotFoundError) as error:
        provider.get_loop(
            start=Coordinate(lat=-40.0, lon=30.0),
            target_distance_m=5000.0,
            seed=1,
        )

    assert "Cannot find point" in str(error.value)


def test_get_route_through_waypoints_parses_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("round_trip_success.json")
    captured_body: dict[str, object] = {}

    def fake_post(
        *_args: object,
        **kwargs: object,
    ) -> httpx.Response:
        captured_body.update(cast(dict[str, object], kwargs["json"]))
        return make_response(200, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    waypoints = [
        Coordinate(lat=40.7128, lon=-74.0060),
        Coordinate(lat=40.7200, lon=-74.0000),
        Coordinate(lat=40.7128, lon=-74.0060),
    ]

    candidate = provider.get_route_through_waypoints(waypoints)

    features = cast(list[dict[str, object]], fixture["features"])
    feature = features[0]
    properties = cast(dict[str, object], feature["properties"])
    summary = cast(dict[str, object], properties["summary"])

    assert candidate.distance_m == float(
        cast(int | float, summary["distance"])
    )

    # No round_trip block -- this is a plain multi-waypoint directions
    # request, unlike get_loop's randomized round trip -- but it must
    # still carry ferry avoidance so repaired/restroom-first routes
    # can't cross the river on ferry lines.
    options = cast(dict[str, object], captured_body["options"])
    assert "round_trip" not in options
    assert options["avoid_features"] == ["ferries"]
    assert captured_body["coordinates"] == [
        [-74.0060, 40.7128],
        [-74.0000, 40.7200],
        [-74.0060, 40.7128],
    ]


def test_get_route_through_waypoints_distance_limit_raises_route_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("error_400_distance_limit.json")

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(400, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    with pytest.raises(RouteNotFoundError):
        provider.get_route_through_waypoints(
            [
                Coordinate(lat=40.7128, lon=-74.0060),
                Coordinate(lat=40.7200, lon=-74.0000),
            ]
        )


def test_bad_key_raises_routing_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("error_403_bad_key.json")

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(403, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    with pytest.raises(RoutingProviderError) as error:
        provider.get_loop(
            start=Coordinate(lat=40.7128, lon=-74.0060),
            target_distance_m=5000.0,
            seed=1,
        )

    assert "Access to this API has been disallowed" in str(error.value)


def test_rate_limited_request_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("round_trip_success.json")
    responses = [
        make_response(429, {"error": "Rate Limit Exceeded"}),
        make_response(200, fixture),
    ]

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)
    # Keep the test instant -- the backoff sleep is the retry's only
    # side effect worth skipping.
    monkeypatch.setattr("app.routing.ors.RATE_LIMIT_BACKOFF_S", 0.0)

    provider = OpenRouteServiceProvider()

    candidate = provider.get_loop(
        start=Coordinate(lat=40.7128, lon=-74.0060),
        target_distance_m=5000.0,
        seed=1,
    )

    assert candidate.distance_m > 0
    assert responses == []


def test_get_loop_parses_extras_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("round_trip_success.json")

    # Built inline rather than editing the shared fixture file -- every
    # other test in this module relies on that fixture having no
    # extras block, so extras are grafted onto a deep-ish copy instead.
    fixture_with_extras = json.loads(json.dumps(fixture))
    feature = cast(
        list[dict[str, object]], fixture_with_extras["features"]
    )[0]
    properties = cast(dict[str, object], feature["properties"])
    properties["extras"] = {
        "waytype": {"values": [[0, 1, 7], [1, 3, 2]]},
        "surface": {"values": [[0, 3, 5]]},
        "steepness": {"values": [[0, 1, 0], [1, 3, 2]]},
    }

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(200, fixture_with_extras)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    candidate = provider.get_loop(
        start=Coordinate(lat=40.7128, lon=-74.0060),
        target_distance_m=5000.0,
        seed=1,
    )

    assert candidate.extras is not None
    assert candidate.extras.waytype_segments == ((0, 1, 7), (1, 3, 2))
    assert candidate.extras.surface_segments == ((0, 3, 5),)
    assert candidate.extras.steepness_segments == (
        (0, 1, 0),
        (1, 3, 2),
    )


def test_get_loop_extras_none_when_absent_from_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture("round_trip_success.json")

    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(200, fixture)

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)

    provider = OpenRouteServiceProvider()

    candidate = provider.get_loop(
        start=Coordinate(lat=40.7128, lon=-74.0060),
        target_distance_m=5000.0,
        seed=1,
    )

    assert candidate.extras is None


def test_rate_limited_request_raises_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(
        *_args: object,
        **_kwargs: object,
    ) -> httpx.Response:
        return make_response(429, {"error": "Rate Limit Exceeded"})

    monkeypatch.setenv("ORS_API_KEY", "test-key")
    monkeypatch.setattr("app.routing.ors.httpx.post", fake_post)
    monkeypatch.setattr("app.routing.ors.RATE_LIMIT_BACKOFF_S", 0.0)

    provider = OpenRouteServiceProvider()

    with pytest.raises(RoutingProviderError):
        provider.get_loop(
            start=Coordinate(lat=40.7128, lon=-74.0060),
            target_distance_m=5000.0,
            seed=1,
        )
