import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import httpx


BACKEND_URL = "http://localhost:8000/routes/with-restroom"
MILES_TO_METERS = 1609.34
REQUEST_TIMEOUT_S = 60.0
RATE_LIMIT_BACKOFF_S = 15.0
MAX_ATTEMPTS_PER_REQUEST = 5

# Each scenario now fires its ~20 ORS calls in parallel bursts rather
# than one at a time, so back-to-back scenarios can blow the ~40/min
# ORS free-tier quota. 35s spacing keeps any rolling one-minute window
# at or under ~40 calls (20 per burst), so scenario results measure
# the algorithm rather than quota contention -- 20s spacing was not
# enough and produced unstable, repair-starved runs.
INTER_SCENARIO_SLEEP_S = 35.0


@dataclass(frozen=True)
class Scenario:
    name: str
    start_lat: float
    start_lon: float
    target_distance_miles: float
    restroom_min_mile: float
    restroom_max_mile: float
    elevation_preference: str


# Spans previously-known trouble spots (Central Park, Battery Park,
# Inwood -- see docs/product-spec.md and prior live-testing sessions)
# plus a spread of generic Manhattan grid points, varying distance,
# restroom mile-range width/position, and elevation preference.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Central Park - Reservoir", 40.7829, -73.9654, 6.0, 1.5, 2.5, "flat"
    ),
    Scenario(
        "Central Park - North Woods",
        40.7969,
        -73.9495,
        8.0,
        2.5,
        3.5,
        "hilly",
    ),
    Scenario("Battery Park", 40.7033, -74.0170, 5.0, 1.0, 2.0, "flat"),
    Scenario(
        "Battery Park - longer", 40.7033, -74.0170, 8.0, 2.0, 3.0, "moderate"
    ),
    Scenario("Inwood Hill Park", 40.8677, -73.9212, 6.0, 1.0, 2.0, "hilly"),
    Scenario(
        "Inwood - longer", 40.8677, -73.9212, 9.0, 3.0, 4.5, "moderate"
    ),
    Scenario("Upper West Side", 40.7870, -73.9754, 5.0, 1.0, 2.0, "flat"),
    Scenario("Midtown", 40.7549, -73.9840, 6.0, 1.5, 2.5, "flat"),
    Scenario("Chelsea", 40.7465, -74.0014, 7.0, 2.0, 3.0, "moderate"),
    Scenario("East Village", 40.7265, -73.9815, 5.0, 1.0, 2.0, "flat"),
    Scenario("Harlem", 40.8116, -73.9465, 6.0, 1.5, 2.5, "moderate"),
    Scenario(
        "Washington Heights", 40.8417, -73.9393, 8.0, 2.5, 3.5, "hilly"
    ),
    Scenario("Lower East Side", 40.7168, -73.9861, 5.0, 1.0, 2.0, "flat"),
    Scenario(
        "Financial District", 40.7075, -74.0113, 4.0, 0.5, 1.5, "flat"
    ),
    Scenario("Yorkville", 40.7754, -73.9482, 6.0, 1.5, 2.5, "moderate"),
    Scenario(
        "Morningside Heights", 40.8065, -73.9629, 7.0, 2.0, 3.0, "hilly"
    ),
    Scenario("Tribeca", 40.7163, -74.0086, 5.0, 1.0, 2.0, "flat"),
)


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    status_code: int
    matched_count: int
    returned_count: int
    best_distance_error_m: float | None
    best_mile_range_error_m: float | None
    response_time_s: float
    error: str | None


def build_payload(scenario: Scenario) -> dict[str, object]:
    return {
        "start_lat": scenario.start_lat,
        "start_lon": scenario.start_lon,
        "target_distance_m": scenario.target_distance_miles
        * MILES_TO_METERS,
        "restroom_min_mile": scenario.restroom_min_mile,
        "restroom_max_mile": scenario.restroom_max_mile,
        "elevation_preference": scenario.elevation_preference,
        "count": 5,
    }


def post_with_retry(
    payload: dict[str, object],
) -> tuple[httpx.Response | None, float]:
    response: httpx.Response | None = None

    for attempt in range(MAX_ATTEMPTS_PER_REQUEST):
        start_time = time.monotonic()

        try:
            response = httpx.post(
                BACKEND_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT_S,
            )
        except httpx.RequestError as exc:
            elapsed = time.monotonic() - start_time
            print(f"  connection error: {exc}")
            return None, elapsed

        elapsed = time.monotonic() - start_time

        if response.status_code == 502 and "Rate Limit" in response.text:
            print(
                f"  rate limited (attempt {attempt + 1}/"
                f"{MAX_ATTEMPTS_PER_REQUEST}), backing off "
                f"{RATE_LIMIT_BACKOFF_S}s"
            )
            time.sleep(RATE_LIMIT_BACKOFF_S)
            continue

        return response, elapsed

    return response, elapsed


def run_scenario(scenario: Scenario) -> ScenarioResult:
    payload = build_payload(scenario)
    response, elapsed = post_with_retry(payload)

    if response is None:
        return ScenarioResult(
            scenario=scenario,
            status_code=0,
            matched_count=0,
            returned_count=0,
            best_distance_error_m=None,
            best_mile_range_error_m=None,
            response_time_s=elapsed,
            error="connection error",
        )

    if response.status_code != 200:
        return ScenarioResult(
            scenario=scenario,
            status_code=response.status_code,
            matched_count=0,
            returned_count=0,
            best_distance_error_m=None,
            best_mile_range_error_m=None,
            response_time_s=elapsed,
            error=response.text[:200],
        )

    routes = cast(list[dict[str, object]], response.json())
    matched = [route for route in routes if route["matched"]]

    distance_errors = [
        cast(float, route["distance_error_m"]) for route in routes
    ]
    range_errors = [
        cast(float, route["mile_range_error_m"]) for route in routes
    ]

    return ScenarioResult(
        scenario=scenario,
        status_code=200,
        matched_count=len(matched),
        returned_count=len(routes),
        best_distance_error_m=(
            min(distance_errors) if distance_errors else None
        ),
        best_mile_range_error_m=(
            min(range_errors) if range_errors else None
        ),
        response_time_s=elapsed,
        error=None,
    )


def print_summary(results: list[ScenarioResult]) -> None:
    successful = [result for result in results if result.status_code == 200]
    with_match = [result for result in successful if result.matched_count > 0]

    print("\n=== Benchmark Summary ===")
    print(f"Scenarios run: {len(results)}")
    print(f"Successful (200): {len(successful)}")

    if successful:
        match_pct = 100 * len(with_match) / len(successful)
        print(
            f"Scenarios with >=1 matched result: {len(with_match)} "
            f"({match_pct:.0f}%)"
        )

    distance_errors = [
        result.best_distance_error_m
        for result in successful
        if result.best_distance_error_m is not None
    ]
    range_errors = [
        result.best_mile_range_error_m
        for result in successful
        if result.best_mile_range_error_m is not None
    ]
    response_times = [result.response_time_s for result in results]

    if distance_errors:
        print(
            "Median best distance_error_m: "
            f"{statistics.median(distance_errors):.1f}"
        )

    if range_errors:
        print(
            "Median best mile_range_error_m: "
            f"{statistics.median(range_errors):.1f}"
        )

    if response_times:
        print(
            "Median response time: "
            f"{statistics.median(response_times):.1f}s"
        )

    print("\nPer-scenario detail:")

    for result in results:
        status = (
            "OK" if result.status_code == 200 else f"FAIL({result.status_code})"
        )
        print(
            f"  {result.scenario.name:28s} {status:10s} "
            f"matched={result.matched_count}/{result.returned_count} "
            f"dist_err={result.best_distance_error_m} "
            f"range_err={result.best_mile_range_error_m} "
            f"t={result.response_time_s:.1f}s"
        )

        if result.error:
            print(f"    error: {result.error}")


RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"


def save_results(results: list[ScenarioResult]) -> Path:
    successful = [result for result in results if result.status_code == 200]
    with_match = [result for result in successful if result.matched_count > 0]
    response_times = [result.response_time_s for result in results]

    payload: dict[str, object] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "scenarios_run": len(results),
            "successful": len(successful),
            "with_matched_result": len(with_match),
            "median_response_time_s": (
                round(statistics.median(response_times), 2)
                if response_times
                else None
            ),
        },
        "scenarios": [
            {
                "name": result.scenario.name,
                "status_code": result.status_code,
                "matched_count": result.matched_count,
                "returned_count": result.returned_count,
                "best_distance_error_m": result.best_distance_error_m,
                "best_mile_range_error_m": result.best_mile_range_error_m,
                "response_time_s": round(result.response_time_s, 2),
                "error": result.error,
            }
            for result in results
        ],
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    output_path = RESULTS_DIR / (
        f"benchmark_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    output_path.write_text(json.dumps(payload, indent=2) + "\n")

    return output_path


def main() -> None:
    results: list[ScenarioResult] = []

    for index, scenario in enumerate(SCENARIOS):
        print(f"Running: {scenario.name} ...")
        result = run_scenario(scenario)
        results.append(result)
        print(
            f"  matched={result.matched_count}/{result.returned_count} "
            f"best_distance_error_m={result.best_distance_error_m} "
            f"best_range_error_m={result.best_mile_range_error_m} "
            f"time={result.response_time_s:.1f}s"
        )

        if index < len(SCENARIOS) - 1:
            time.sleep(INTER_SCENARIO_SLEEP_S)

    print_summary(results)
    output_path = save_results(results)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
