import threading

import pytest

from app.llm.guard import ModelGuard
from app.llm.transport import PermanentModelError, TransientModelError
from app.resilience import CircuitBreaker, RetryPolicy
from app.service.diagnosis_model import ModelUnavailable


def guard(attempts: int = 2, threshold: int = 5, bulkhead: int = 2) -> ModelGuard:
    return ModelGuard(
        CircuitBreaker("model", threshold, 60),
        RetryPolicy(attempts, 0.1, 1),
        bulkhead,
        sleep=lambda _: None,
    )


def test_a_transient_failure_is_retried_once():
    calls = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise TransientModelError
        return "ok"

    assert guard().call(flaky) == "ok"
    assert len(calls) == 2


def test_a_permanent_failure_is_not_retried():
    calls = []

    def rejected() -> str:
        calls.append(1)
        raise PermanentModelError

    with pytest.raises(ModelUnavailable):
        guard().call(rejected)

    assert len(calls) == 1


def test_persistent_transient_failures_end_as_unavailable():
    def down() -> str:
        raise TransientModelError

    with pytest.raises(ModelUnavailable):
        guard().call(down)


def test_the_open_breaker_rejects_without_calling_the_model():
    protected = guard(attempts=1, threshold=2)
    calls = []

    def down() -> str:
        calls.append(1)
        raise TransientModelError

    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            protected.call(down)
    with pytest.raises(ModelUnavailable, match="breaker"):
        protected.call(down)

    assert len(calls) == 2


def test_a_full_bulkhead_fails_at_once_and_a_slot_is_freed_afterwards():
    protected = guard(bulkhead=1)
    inside, leave = threading.Event(), threading.Event()

    def slow() -> str:
        inside.set()
        assert leave.wait(timeout=5)
        return "slow"

    worker = threading.Thread(target=protected.call, args=(slow,))
    worker.start()
    assert inside.wait(timeout=5)

    with pytest.raises(ModelUnavailable, match="bulkhead"):
        protected.call(lambda: "never runs")

    leave.set()
    worker.join(timeout=5)
    assert protected.call(lambda: "again") == "again"


def test_a_slot_is_freed_after_a_failure():
    protected = guard(bulkhead=1, attempts=1)

    def down() -> str:
        raise PermanentModelError

    for _ in range(3):
        with pytest.raises(ModelUnavailable, match="failed"):
            protected.call(down)
