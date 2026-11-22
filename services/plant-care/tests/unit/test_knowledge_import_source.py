import json
from collections.abc import Callable

import httpx
import pytest

from app.knowledge_import.source import (
    FetchPolicy,
    NotFound,
    PlantSolveSource,
    SourceError,
    create_http_client,
)

POLICY = FetchPolicy(
    pause_seconds=2.0,
    max_response_bytes=1000,
    max_attempts=3,
    retry_base_delay_seconds=1.0,
    retry_max_delay_seconds=30.0,
)


class Harness:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        client = create_http_client(5.0, httpx.MockTransport(record))
        self.source = PlantSolveSource(
            client, POLICY, sleep=self.sleeps.append, jitter=lambda low, high: high
        )


def serving(*responses: httpx.Response | Exception) -> Harness:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return Harness(handler)


def ok(payload: object = None) -> httpx.Response:
    return httpx.Response(200, json={"slug": "lavender"} if payload is None else payload)


def test_a_plant_is_fetched_over_https_from_the_allowed_host_with_an_identifying_agent():
    harness = serving(ok())

    record = harness.source.plant("lavender")

    request = harness.requests[0]
    assert record == {"slug": "lavender"}
    assert str(request.url) == "https://www.plantsolve.com/api/v1/plants/lavender.json"
    assert request.headers["User-Agent"] == "growpath-kb-import/1.0 (open data, CC BY 4.0)"


def test_each_endpoint_has_its_own_path_and_shape():
    harness = serving(ok([]), ok({"slug": "overwatering"}), ok([]))

    harness.source.problems_index()
    harness.source.problem("overwatering")
    harness.source.relationships()

    assert [request.url.path for request in harness.requests] == [
        "/api/v1/problems/index.json",
        "/api/v1/problems/overwatering.json",
        "/api/v1/relationships.json",
    ]


def test_requests_are_paused_but_the_first_one_is_not():
    harness = serving(ok())

    harness.source.plant("lavender")
    harness.source.plant("aloe-vera")
    harness.source.plant("cactus")

    assert harness.sleeps == [2.0, 2.0]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://www.plantsolve.com/api/v1/",
        "https://example.com/api/v1/",
        "https://www.plantsolve.com.evil.test/api/v1/",
        "https://user@evil.test@www.plantsolve.com.evil.test/",
    ],
)
def test_any_host_but_plantsolve_over_https_is_refused(base_url: str):
    with pytest.raises(ValueError):
        PlantSolveSource(create_http_client(5.0), POLICY, base_url=base_url)


@pytest.mark.parametrize("slug", ["../secrets", "a/b", "A", "", "x.json?y=1", "a b"])
def test_a_slug_cannot_change_the_path(slug: str):
    harness = serving(ok())

    with pytest.raises(SourceError):
        harness.source.plant(slug)

    assert harness.requests == []


def test_a_429_is_retried_after_the_pause_the_host_asked_for():
    harness = serving(httpx.Response(429, headers={"Retry-After": "7"}), ok())

    harness.source.plant("lavender")

    assert len(harness.requests) == 2
    assert harness.sleeps == [7.0]


def test_a_long_retry_after_is_capped():
    harness = serving(httpx.Response(503, headers={"Retry-After": "3600"}), ok())

    harness.source.plant("lavender")

    assert harness.sleeps == [30.0]


def test_without_retry_after_the_backoff_doubles():
    harness = serving(httpx.Response(503), httpx.Response(429), ok())

    harness.source.plant("lavender")

    assert harness.sleeps == [1.0, 2.0]


def test_a_transport_error_is_retried_and_gives_up_after_the_attempts():
    harness = serving(httpx.ConnectError("down"))

    with pytest.raises(SourceError, match="after 3 attempts"):
        harness.source.plant("lavender")

    assert len(harness.requests) == 3


def test_a_missing_record_is_not_found_and_not_retried():
    harness = serving(httpx.Response(404))

    with pytest.raises(NotFound):
        harness.source.plant("nothing")

    assert len(harness.requests) == 1


def test_other_errors_fail_at_once():
    harness = serving(httpx.Response(500))

    with pytest.raises(SourceError, match="HTTP 500"):
        harness.source.plant("lavender")

    assert len(harness.requests) == 1


def test_a_redirect_is_not_followed():
    harness = serving(httpx.Response(302, headers={"Location": "https://evil.test/x"}))

    with pytest.raises(SourceError, match="HTTP 302"):
        harness.source.plant("lavender")

    assert len(harness.requests) == 1


def test_a_response_over_the_size_cap_is_refused_while_reading():
    harness = serving(httpx.Response(200, content=json.dumps({"x": "a" * 2000}).encode()))

    with pytest.raises(SourceError, match="larger than 1000 bytes"):
        harness.source.plant("lavender")


def test_a_declared_size_over_the_cap_is_refused_before_reading():
    harness = serving(httpx.Response(200, headers={"Content-Length": "5000"}, content=b"{}"))

    with pytest.raises(SourceError, match="larger than 1000 bytes"):
        harness.source.plant("lavender")


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"<html>"), ok([1])])
def test_a_body_that_is_not_the_expected_json_is_an_error(response: httpx.Response):
    harness = serving(response)

    with pytest.raises(SourceError):
        harness.source.plant("lavender")


def test_a_list_endpoint_must_return_an_array():
    harness = serving(ok({"not": "a list"}))

    with pytest.raises(SourceError, match="JSON array"):
        harness.source.relationships()
