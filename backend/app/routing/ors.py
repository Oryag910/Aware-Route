import os
import time
from typing import cast

import httpx
from dotenv import load_dotenv

from app.routing.errors import RouteNotFoundError, RoutingProviderError
from app.routing.provider import Coordinate, RouteCandidate, RoutePoint


ORS_URL = (
    "https://api.openrouteservice.org/"
    "v2/directions/foot-walking/geojson"
)

# The free tier allows ~40 requests/minute, and a request that lands
# mid-burst can get a 429 that would otherwise abort an entire repair
# chain -- one short retry converts most of those into successes.
# Rejected (429) calls don't count against the quota.
RATE_LIMIT_STATUS = 429
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_S = 2.0


def extract_error_message(response: httpx.Response) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return response.text or f"ORS returned HTTP {response.status_code}"

    if isinstance(body, dict):
        error_body = body.get("error")

        if isinstance(error_body, dict):
            message = error_body.get("message")
            code = error_body.get("code")

            if isinstance(message, str):
                if isinstance(code, int):
                    return f"ORS error {code}: {message}"

                return message

        if isinstance(error_body, str):
            return error_body

    return response.text or f"ORS returned HTTP {response.status_code}"


class OpenRouteServiceProvider:
    def __init__(self) -> None:
        load_dotenv()
        self.api_key = os.environ["ORS_API_KEY"]

    def _send_request(self, body: dict[str, object]) -> httpx.Response:
        headers: dict[str, str] = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                response = httpx.post(
                    ORS_URL,
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
                response.raise_for_status()

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                message = extract_error_message(exc.response)

                if (
                    status_code == RATE_LIMIT_STATUS
                    and attempt < RATE_LIMIT_MAX_RETRIES
                ):
                    time.sleep(RATE_LIMIT_BACKOFF_S)
                    continue

                if status_code in {400, 404}:
                    raise RouteNotFoundError(message) from exc

                raise RoutingProviderError(message) from exc

            except httpx.RequestError as exc:
                raise RoutingProviderError(
                    f"Could not connect to OpenRouteService: {exc}"
                ) from exc

            return response

        # Unreachable: the final attempt either returned or raised.
        raise RoutingProviderError(
            "OpenRouteService rate limit retries exhausted."
        )

    def _parse_route_response(
        self,
        response: httpx.Response,
    ) -> RouteCandidate:
        try:
            data: dict[str, object] = response.json()

            features = cast(
                list[dict[str, object]],
                data["features"],
            )

            if not features:
                raise RouteNotFoundError(
                    "OpenRouteService returned no route."
                )

            feature = features[0]

            properties = cast(
                dict[str, object],
                feature["properties"],
            )

            summary = cast(
                dict[str, object],
                properties["summary"],
            )

            distance_m = float(
                cast(int | float, summary["distance"])
            )

            elevation_gain_m = float(
                cast(int | float, properties["ascent"])
            )

            geometry_data = cast(
                dict[str, object],
                feature["geometry"],
            )

            raw_coordinates = cast(
                list[list[int | float]],
                geometry_data["coordinates"],
            )

            geometry = tuple(
                RoutePoint(
                    lat=float(coordinate[1]),
                    lon=float(coordinate[0]),
                    elevation_m=float(coordinate[2]),
                )
                for coordinate in raw_coordinates
            )

        except RouteNotFoundError:
            raise

        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise RoutingProviderError(
                "OpenRouteService returned an unexpected response."
            ) from exc

        return RouteCandidate(
            geometry=geometry,
            distance_m=distance_m,
            elevation_gain_m=elevation_gain_m,
        )

    def get_loop(
        self,
        start: Coordinate,
        target_distance_m: float,
        seed: int,
    ) -> RouteCandidate:
        body: dict[str, object] = {
            "coordinates": [[start.lon, start.lat]],
            "elevation": True,
            "options": {
                "round_trip": {
                    "length": target_distance_m,
                    "points": 3,
                    "seed": seed,
                }
            },
        }

        response = self._send_request(body)
        return self._parse_route_response(response)

    def get_route_through_waypoints(
        self,
        waypoints: list[Coordinate],
    ) -> RouteCandidate:
        body: dict[str, object] = {
            "coordinates": [
                [waypoint.lon, waypoint.lat] for waypoint in waypoints
            ],
            "elevation": True,
        }

        response = self._send_request(body)
        return self._parse_route_response(response)
