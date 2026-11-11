import logging
import time
import uuid
from collections.abc import Callable

import httpx
from growpath_observability import CORRELATION_HEADER, get_correlation_id

from app.config import Settings
from app.resilience import CircuitBreaker, CircuitOpen, RetryPolicy, retry_with_jitter
from app.service.errors import AiQuotaExhausted, AiQuotaUnavailable, ReservationUnusable
from app.service.model import Tier

logger = logging.getLogger(__name__)

AI_RESERVATIONS = "/v1/quotas/ai/reservations"
QUOTA_EXHAUSTED = "quota_exhausted"


class EntitlementFailure(Exception):
    """A transport error or a 5xx answer: repeatable, and counted by the breaker."""


class EntitlementClient:
    """REST client of the entitlement service: AI quota reservations and the tier.

    Every call is safe to repeat: reserve is idempotent by reservation id, commit and release move
    the reservation to a terminal state and answer the same way when repeated, and the tier lookup
    only reads. So all calls are retried on transport errors and 5xx answers.
    """

    def __init__(
        self,
        http: httpx.Client,
        breaker: CircuitBreaker,
        retry: RetryPolicy,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._breaker = breaker
        self._retry = retry
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        body = {"reservation_id": str(reservation_id), "user_id": str(user_id)}
        response = self._send("reserve", "POST", AI_RESERVATIONS, body)
        if response.status_code in (200, 201):
            return
        if response.status_code == 409:
            raise self._conflict(response)
        self._unexpected("reserve", response)

    def commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        path = f"{AI_RESERVATIONS}/{reservation_id}/commit"
        response = self._send("commit", "POST", path, {"user_id": str(user_id)})
        if response.status_code == 200:
            return
        if response.status_code in (404, 409):
            raise ReservationUnusable
        self._unexpected("commit", response)

    def release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        path = f"{AI_RESERVATIONS}/{reservation_id}/release"
        response = self._send("release", "POST", path, {"user_id": str(user_id)})
        if response.status_code in (200, 404):
            return
        if response.status_code == 409:
            raise ReservationUnusable
        self._unexpected("release", response)

    def tier_of(self, user_id: uuid.UUID) -> Tier:
        response = self._send("tier", "GET", f"/v1/entitlements/{user_id}", None)
        if response.status_code != 200:
            self._unexpected("tier", response)
        try:
            return Tier(response.json()["tier"])
        except (ValueError, KeyError, TypeError) as error:
            logger.error("entitlement answered with an unknown tier", extra={"call": "tier"})
            raise AiQuotaUnavailable from error

    def _send(self, call: str, method: str, path: str, body: dict | None) -> httpx.Response:
        try:
            return retry_with_jitter(
                lambda: self._breaker.call(
                    lambda: self._send_once(call, method, path, body), (EntitlementFailure,)
                ),
                self._retry,
                (EntitlementFailure,),
                sleep=self._sleep,
            )
        except CircuitOpen as error:
            logger.warning("entitlement call rejected by the open breaker", extra={"call": call})
            raise AiQuotaUnavailable from error
        except EntitlementFailure as error:
            raise AiQuotaUnavailable from error

    def _send_once(self, call: str, method: str, path: str, body: dict | None) -> httpx.Response:
        headers = {}
        if correlation_id := get_correlation_id():
            headers[CORRELATION_HEADER] = correlation_id
        started = time.monotonic()
        try:
            response = self._http.request(method, path, json=body, headers=headers)
        except httpx.HTTPError as error:
            self._log(call, started, outcome=type(error).__name__)
            raise EntitlementFailure from error
        self._log(call, started, status=response.status_code)
        if response.status_code >= 500:
            raise EntitlementFailure(response.status_code)
        return response

    @staticmethod
    def _log(call: str, started: float, **result: object) -> None:
        logger.info(
            "outgoing call to entitlement",
            extra={
                "target": "entitlement",
                "call": call,
                "duration_ms": round((time.monotonic() - started) * 1000),
                **result,
            },
        )

    @staticmethod
    def _conflict(response: httpx.Response) -> Exception:
        if _problem_code(response) == QUOTA_EXHAUSTED:
            return AiQuotaExhausted()
        return ReservationUnusable()

    @staticmethod
    def _unexpected(call: str, response: httpx.Response) -> None:
        logger.error(
            "entitlement answered unexpectedly",
            extra={"call": call, "status": response.status_code},
        )
        raise AiQuotaUnavailable


def _problem_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("code")
    except (ValueError, AttributeError):
        return None


def create_entitlement_client(settings: Settings) -> EntitlementClient:
    http = httpx.Client(
        base_url=settings.entitlement_url,
        timeout=httpx.Timeout(
            connect=settings.entitlement_connect_timeout_seconds,
            read=settings.entitlement_read_timeout_seconds,
            write=settings.entitlement_read_timeout_seconds,
            pool=settings.entitlement_connect_timeout_seconds,
        ),
        limits=httpx.Limits(max_connections=settings.entitlement_max_connections),
    )
    breaker = CircuitBreaker(
        "entitlement",
        settings.entitlement_breaker_failure_threshold,
        settings.entitlement_breaker_reset_seconds,
    )
    retry = RetryPolicy(
        settings.entitlement_max_attempts,
        settings.entitlement_retry_base_delay_seconds,
        settings.entitlement_retry_max_delay_seconds,
    )
    return EntitlementClient(http, breaker, retry)
