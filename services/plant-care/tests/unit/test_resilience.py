import pytest

from app.resilience import BreakerState, CircuitBreaker, CircuitOpen, RetryPolicy, retry_with_jitter


class Boom(Exception):
    pass


class Fine(Exception):
    pass


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def fail() -> None:
    raise Boom


def succeed() -> str:
    return "ok"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker("test", failure_threshold=3, reset_timeout_seconds=10, clock=clock)


def trip(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        with pytest.raises(Boom):
            breaker.call(fail, (Boom,))


def test_the_breaker_opens_after_consecutive_failures_and_rejects_calls(breaker: CircuitBreaker):
    trip(breaker)

    assert breaker.state is BreakerState.OPEN
    with pytest.raises(CircuitOpen):
        breaker.call(succeed, (Boom,))


def test_a_success_resets_the_failure_count(breaker: CircuitBreaker):
    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(fail, (Boom,))
    breaker.call(succeed, (Boom,))

    with pytest.raises(Boom):
        breaker.call(fail, (Boom,))

    assert breaker.state is BreakerState.CLOSED


def test_exceptions_outside_the_counted_ones_do_not_open_the_breaker(breaker: CircuitBreaker):
    def refuse() -> None:
        raise Fine

    for _ in range(5):
        with pytest.raises(Fine):
            breaker.call(refuse, (Boom,))

    assert breaker.state is BreakerState.CLOSED


def test_after_the_reset_timeout_one_probe_is_allowed_and_success_closes(
    breaker: CircuitBreaker, clock: FakeClock
):
    trip(breaker)
    clock.now += 10

    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.call(succeed, (Boom,)) == "ok"
    assert breaker.state is BreakerState.CLOSED


def test_a_failed_probe_reopens_the_breaker_for_a_new_timeout(
    breaker: CircuitBreaker, clock: FakeClock
):
    trip(breaker)
    clock.now += 10
    with pytest.raises(Boom):
        breaker.call(fail, (Boom,))

    assert breaker.state is BreakerState.OPEN
    clock.now += 9
    with pytest.raises(CircuitOpen):
        breaker.call(succeed, (Boom,))


def test_only_one_probe_runs_at_a_time_in_half_open(breaker: CircuitBreaker, clock: FakeClock):
    trip(breaker)
    clock.now += 10

    def probe() -> str:
        with pytest.raises(CircuitOpen):
            breaker.call(succeed, (Boom,))
        return "probed"

    assert breaker.call(probe, (Boom,)) == "probed"


def test_a_retry_runs_until_success_with_a_jittered_growing_ceiling():
    attempts, delays = [], []

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise Boom
        return "ok"

    result = retry_with_jitter(
        flaky,
        RetryPolicy(attempts=3, base_delay_seconds=0.1, max_delay_seconds=0.15),
        (Boom,),
        sleep=delays.append,
        jitter=lambda low, high: high,
    )

    assert result == "ok"
    assert delays == [0.1, 0.15]


def test_the_last_failure_is_raised_when_attempts_run_out():
    calls = []

    def always() -> None:
        calls.append(1)
        raise Boom

    with pytest.raises(Boom):
        retry_with_jitter(
            always, RetryPolicy(2, 0.1, 1), (Boom,), sleep=lambda _: None, jitter=lambda a, b: b
        )

    assert len(calls) == 2


def test_other_exceptions_are_not_retried():
    calls = []

    def refuse() -> None:
        calls.append(1)
        raise Fine

    with pytest.raises(Fine):
        retry_with_jitter(refuse, RetryPolicy(3, 0.1, 1), (Boom,), sleep=lambda _: None)

    assert len(calls) == 1
