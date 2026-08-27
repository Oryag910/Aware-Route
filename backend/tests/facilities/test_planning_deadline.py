import time

import pytest

from app.facilities.planning_deadline import DEFAULT_BUDGET_S, PlanningDeadline


def test_default_budget_used_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROUTE_PLANNING_BUDGET_S", raising=False)
    deadline = PlanningDeadline()
    assert deadline.budget_s == DEFAULT_BUDGET_S


def test_explicit_budget_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "5")
    deadline = PlanningDeadline(budget_s=12.0)
    assert deadline.budget_s == 12.0


def test_valid_env_value_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "10")
    assert PlanningDeadline().budget_s == 10.0


def test_malformed_env_value_falls_back_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "not-a-number")
    assert PlanningDeadline().budget_s == DEFAULT_BUDGET_S


def test_zero_env_value_falls_back_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero/negative budget would make every planner call a no-op (or,
    if `expired()`'s comparison were ever inverted, unbounded) -- neither
    is an intentional configuration."""
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "0")
    assert PlanningDeadline().budget_s == DEFAULT_BUDGET_S


def test_negative_env_value_falls_back_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_PLANNING_BUDGET_S", "-5")
    assert PlanningDeadline().budget_s == DEFAULT_BUDGET_S


def test_not_expired_immediately() -> None:
    deadline = PlanningDeadline(budget_s=5.0)
    assert not deadline.expired()
    assert deadline.remaining() > 0.0


def test_expires_after_budget_elapses() -> None:
    deadline = PlanningDeadline(budget_s=0.01)
    time.sleep(0.02)
    assert deadline.expired()
    assert deadline.remaining() == 0.0


def test_explicit_zero_budget_is_immediately_expired() -> None:
    """Unlike the env-var path, an explicit `budget_s=0` argument is a
    deliberate zero-budget deadline (used by tests to force an
    immediate-expiry planner path), not a malformed config value."""
    deadline = PlanningDeadline(budget_s=0.0)
    assert deadline.expired()


def test_explicit_negative_budget_is_immediately_expired() -> None:
    deadline = PlanningDeadline(budget_s=-1.0)
    assert deadline.expired()
    assert deadline.remaining() == 0.0
