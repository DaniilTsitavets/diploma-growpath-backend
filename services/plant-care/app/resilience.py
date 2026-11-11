import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitOpen(Exception):
    """The breaker rejected the call without trying the dependency."""


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after consecutive failures; after the reset timeout lets one probe call through."""

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state_now()

    def call[T](self, operation: Callable[[], T], counted: tuple[type[Exception], ...]) -> T:
        """Run `operation`; exceptions in `counted` are failures, any other outcome is healthy."""
        self._admit()
        try:
            result = operation()
        except counted:
            self._record_failure()
            raise
        except Exception:
            self._record_success()
            raise
        self._record_success()
        return result

    def _state_now(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self._clock() - self._opened_at >= self._reset_timeout_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = False
        return self._state

    def _admit(self) -> None:
        with self._lock:
            state = self._state_now()
            if state is BreakerState.OPEN or (
                state is BreakerState.HALF_OPEN and self._probe_in_flight
            ):
                raise CircuitOpen(self._name)
            if state is BreakerState.HALF_OPEN:
                self._probe_in_flight = True

    def _record_success(self) -> None:
        with self._lock:
            if self._state is BreakerState.HALF_OPEN:
                logger.info("circuit breaker closed", extra={"breaker": self._name})
            self._state = BreakerState.CLOSED
            self._failures = 0
            self._probe_in_flight = False

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self._failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()
                self._probe_in_flight = False
                logger.warning("circuit breaker opened", extra={"breaker": self._name})


@dataclass(frozen=True)
class RetryPolicy:
    """`attempts` counts the first call; the delay before retry n is random up to base * 2^(n-1)."""

    attempts: int
    base_delay_seconds: float
    max_delay_seconds: float


def retry_with_jitter[T](
    operation: Callable[[], T],
    policy: RetryPolicy,
    retry_on: tuple[type[Exception], ...],
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> T:
    attempt = 1
    while True:
        try:
            return operation()
        except retry_on:
            if attempt >= policy.attempts:
                raise
            ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (attempt - 1))
            sleep(jitter(0, ceiling))
            attempt += 1
