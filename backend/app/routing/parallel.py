from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar


# Cap on simultaneous ORS calls from any one request. Threads (not
# asyncio) provide the parallelism since RoutingProvider stays
# synchronous. Kept below the ORS burst tolerance so a single request
# can't trip the provider's own rate limiting. Tests monkeypatch this
# to 1 to keep list-popping fake providers deterministic.
MAX_PARALLEL_ROUTING_CALLS = 6

T = TypeVar("T")


def run_concurrently(tasks: list[Callable[[], T]]) -> list[T | Exception]:
    """Runs zero-arg callables on a thread pool and returns their
    results in submission order (not completion order), so callers can
    zip results back up with the inputs that produced them. A task
    that raises has its exception captured in its slot rather than
    propagated, so one failing call doesn't cancel the others."""

    if not tasks:
        return []

    # Read the module constant at call time rather than binding it in
    # a default argument, so monkeypatching it in tests takes effect.
    max_workers = min(MAX_PARALLEL_ROUTING_CALLS, len(tasks))

    results: list[T | Exception] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(task) for task in tasks]

        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(exc)

    return results
