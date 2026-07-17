import time
from collections.abc import Callable

import pytest

from app.routing.parallel import run_concurrently


def test_run_concurrently_returns_empty_list_for_no_tasks() -> None:
    assert run_concurrently([]) == []


def test_run_concurrently_preserves_submission_order() -> None:
    # Tasks sleep in reverse-completion order (the last task finishes
    # first) so a naive completion-order implementation would return
    # results out of order.
    def make_task(value: int, delay_s: float) -> Callable[[], int]:
        def task() -> int:
            time.sleep(delay_s)
            return value

        return task

    tasks = [
        make_task(1, 0.03),
        make_task(2, 0.02),
        make_task(3, 0.01),
    ]

    results = run_concurrently(tasks)

    assert results == [1, 2, 3]


def test_run_concurrently_captures_exceptions_in_slot() -> None:
    failure = ValueError("boom")

    def ok() -> str:
        return "fine"

    def fails() -> str:
        raise failure

    results = run_concurrently([ok, fails, ok])

    assert results[0] == "fine"
    assert results[1] is failure
    assert results[2] == "fine"


def test_run_concurrently_respects_max_parallel_routing_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routing.parallel.MAX_PARALLEL_ROUTING_CALLS", 1
    )

    order: list[int] = []

    def make_task(value: int) -> Callable[[], int]:
        def task() -> int:
            order.append(value)
            return value

        return task

    results = run_concurrently(
        [make_task(1), make_task(2), make_task(3)]
    )

    # With a single worker, tasks must run strictly in submission
    # order rather than interleaving.
    assert order == [1, 2, 3]
    assert results == [1, 2, 3]
