import json
import logging
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

ALLOWED_HOST = "www.plantsolve.com"
BASE_URL = f"https://{ALLOWED_HOST}/api/v1/"
USER_AGENT = "growpath-kb-import/1.0 (open data, CC BY 4.0)"
RETRY_STATUSES = frozenset({429, 503})
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SourceError(Exception):
    """The source could not deliver a usable response."""


class NotFound(SourceError):
    """The source has no such record."""


class KnowledgeSource(Protocol):
    def plant(self, slug: str) -> Mapping[str, Any]: ...

    def problems_index(self) -> list[Mapping[str, Any]]: ...

    def problem(self, slug: str) -> Mapping[str, Any]: ...

    def relationships(self) -> list[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class FetchPolicy:
    """`max_attempts` counts the first request; `retry_max_delay_seconds` also caps Retry-After."""

    pause_seconds: float
    max_response_bytes: int
    max_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float


class _Retryable(Exception):
    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


def create_http_client(
    timeout_seconds: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Redirects are not followed, so a response can never move the request to another host."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout_seconds,
        follow_redirects=False,
        transport=transport,
    )


class PlantSolveSource:
    """Reads the open PlantSolve data, one polite request at a time."""

    def __init__(
        self,
        client: httpx.Client,
        policy: FetchPolicy,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or parts.hostname != ALLOWED_HOST:
            raise ValueError(f"only https://{ALLOWED_HOST} may be used as the source")
        self._client = client
        self._policy = policy
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self._sleep = sleep
        self._jitter = jitter
        self._requested = False

    def plant(self, slug: str) -> Mapping[str, Any]:
        return self._get_object(f"plants/{_checked_slug(slug)}.json")

    def problems_index(self) -> list[Mapping[str, Any]]:
        return self._get_list("problems/index.json")

    def problem(self, slug: str) -> Mapping[str, Any]:
        return self._get_object(f"problems/{_checked_slug(slug)}.json")

    def relationships(self) -> list[Mapping[str, Any]]:
        return self._get_list("relationships.json")

    def _get_object(self, path: str) -> Mapping[str, Any]:
        data = self._get_json(path)
        if not isinstance(data, dict):
            raise SourceError(f"{path}: expected a JSON object")
        return data

    def _get_list(self, path: str) -> list[Mapping[str, Any]]:
        data = self._get_json(path)
        if not isinstance(data, list):
            raise SourceError(f"{path}: expected a JSON array")
        return data

    def _get_json(self, path: str) -> Any:
        body = self._get_body(path)
        try:
            return json.loads(body)
        except ValueError as error:
            raise SourceError(f"{path}: the response is not valid JSON") from error

    def _get_body(self, path: str) -> bytes:
        url = f"{self._base_url}{path}"
        self._pause_between_requests()
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                return self._fetch_once(url)
            except _Retryable as retry:
                if attempt == self._policy.max_attempts:
                    raise SourceError(f"{path}: {retry} after {attempt} attempts") from retry
                delay = self._retry_delay(attempt, retry.retry_after)
                logger.warning(
                    "plantsolve request will be retried",
                    extra={"path": path, "reason": str(retry), "delay_seconds": delay},
                )
                self._sleep(delay)
        raise AssertionError("max_attempts is at least 1")

    def _fetch_once(self, url: str) -> bytes:
        try:
            with self._client.stream("GET", url) as response:
                return self._read(response, url)
        except httpx.TransportError as error:
            raise _Retryable(type(error).__name__) from error

    def _read(self, response: httpx.Response, url: str) -> bytes:
        status = response.status_code
        if status == 404:
            raise NotFound(url)
        if status in RETRY_STATUSES:
            raise _Retryable(f"HTTP {status}", _retry_after(response))
        if status != 200:
            raise SourceError(f"{url}: HTTP {status}")
        limit = self._policy.max_response_bytes
        declared = response.headers.get("Content-Length", "")
        if declared.isdecimal() and int(declared) > limit:
            raise SourceError(f"{url}: the response is larger than {limit} bytes")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise SourceError(f"{url}: the response is larger than {limit} bytes")
        return bytes(body)

    def _pause_between_requests(self) -> None:
        if self._requested:
            self._sleep(self._policy.pause_seconds)
        self._requested = True

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        cap = self._policy.retry_max_delay_seconds
        if retry_after is not None:
            return min(retry_after, cap)
        return self._jitter(0, min(cap, self._policy.retry_base_delay_seconds * 2 ** (attempt - 1)))


def _checked_slug(slug: str) -> str:
    if not SLUG.fullmatch(slug):
        raise SourceError(f"invalid slug {slug!r}")
    return slug


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    return float(value) if value.isdecimal() else None
