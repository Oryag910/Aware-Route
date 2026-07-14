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
