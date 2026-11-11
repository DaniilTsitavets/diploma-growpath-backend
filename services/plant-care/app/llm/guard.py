import threading
import time
from collections.abc import Callable

from app.llm.transport import ModelCallFailed, TransientModelError
from app.resilience import CircuitBreaker, CircuitOpen, RetryPolicy, retry_with_jitter
from app.service.diagnosis_model import ModelUnavailable


class ModelGuard:
    """Bulkhead, circuit breaker and one retry with jitter around every call to the model.

    The bulkhead never queues: when all slots are busy the call fails at once, so a slow model
    cannot hold the request threads of the service.
    """

    def __init__(
        self,
        breaker: CircuitBreaker,
        retry: RetryPolicy,
        bulkhead_size: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._breaker = breaker
        self._retry = retry
        self._bulkhead = threading.BoundedSemaphore(bulkhead_size)
        self._sleep = sleep

    def call[T](self, operation: Callable[[], T]) -> T:
        """Raises ModelUnavailable when the model cannot be used."""
        if not self._bulkhead.acquire(blocking=False):
            raise ModelUnavailable("the model bulkhead is full")
        try:
            return retry_with_jitter(
                lambda: self._breaker.call(operation, (ModelCallFailed,)),
                self._retry,
                (TransientModelError,),
                sleep=self._sleep,
            )
        except CircuitOpen as error:
            raise ModelUnavailable("the model circuit breaker is open") from error
        except ModelCallFailed as error:
            raise ModelUnavailable("the model call failed") from error
        finally:
            self._bulkhead.release()
