import json
import uuid

import httpx
import pytest
from growpath_observability import CORRELATION_HEADER, set_correlation_id

from app.quota import EntitlementClient, create_entitlement_client
from app.resilience import CircuitBreaker, RetryPolicy
from app.service.errors import AiQuotaExhausted, AiQuotaUnavailable, ReservationUnusable
from app.service.model import Tier
from tests.unit.settings import make_settings

RESERVATION = uuid.uuid4()
USER = uuid.uuid4()
BASE = "/v1/quotas/ai/reservations"


class Upstream:
    """Answers the client's requests from a script and records what it received."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def problem(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"status": status, "code": code})


def build(*answers: httpx.Response | Exception, attempts: int = 2, threshold: int = 5):
    upstream = Upstream(*answers)
    http = httpx.Client(base_url="http://entitlement", transport=httpx.MockTransport(upstream))
    sleeps: list[float] = []
    client = EntitlementClient(
        http,
        CircuitBreaker("entitlement", threshold, 30),
        RetryPolicy(attempts, 0.1, 1),
        sleep=sleeps.append,
    )
    return client, upstream, sleeps


def reserved(status: int = 201) -> httpx.Response:
    return httpx.Response(
        status, json={"reservation_id": str(RESERVATION), "status": "reserved", "expires_at": "x"}
    )


def test_reserve_posts_the_reservation_and_the_owner():
    client, upstream, _ = build(reserved())

    client.reserve(RESERVATION, USER)

    (request,) = upstream.requests
    assert (request.method, request.url.path) == ("POST", BASE)
    assert json.loads(request.content) == {
        "reservation_id": str(RESERVATION),
        "user_id": str(USER),
    }


def test_a_replayed_reservation_answering_200_is_a_success():
    client, _, _ = build(reserved(200))

    client.reserve(RESERVATION, USER)


def test_reserve_maps_an_exhausted_quota_to_the_domain_error():
    client, upstream, _ = build(problem(409, "quota_exhausted"))

    with pytest.raises(AiQuotaExhausted):
        client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 1


@pytest.mark.parametrize("code", ["reservation_unusable", "reservation_id_in_use"])
def test_reserve_maps_an_unusable_reservation_id(code: str):
    client, _, _ = build(problem(409, code))

    with pytest.raises(ReservationUnusable):
        client.reserve(RESERVATION, USER)


def test_commit_posts_to_the_commit_path():
    client, upstream, _ = build(reserved(200))

    client.commit(RESERVATION, USER)

    assert upstream.requests[0].url.path == f"{BASE}/{RESERVATION}/commit"


@pytest.mark.parametrize("status", [404, 409])
def test_commit_of_an_expired_or_released_reservation_is_unusable(status: int):
    client, _, _ = build(problem(status, "reservation_unusable"))

    with pytest.raises(ReservationUnusable):
        client.commit(RESERVATION, USER)


@pytest.mark.parametrize("status", [200, 404])
def test_release_succeeds_for_a_released_or_unknown_reservation(status: int):
    client, upstream, _ = build(httpx.Response(status, json={}))

    client.release(RESERVATION, USER)

    assert upstream.requests[0].url.path == f"{BASE}/{RESERVATION}/release"


def test_releasing_a_committed_reservation_is_unusable():
    client, _, _ = build(problem(409, "reservation_unusable"))

    with pytest.raises(ReservationUnusable):
        client.release(RESERVATION, USER)


def test_a_503_is_retried_with_jitter_and_then_succeeds():
    client, upstream, sleeps = build(httpx.Response(503), reserved())

    client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 2
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 0.1


def test_a_timeout_on_every_attempt_means_unavailable():
    client, upstream, _ = build(httpx.ReadTimeout("slow"), attempts=3)

    with pytest.raises(AiQuotaUnavailable):
        client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 3


def test_a_connection_error_means_unavailable():
    client, _, _ = build(httpx.ConnectError("down"))

    with pytest.raises(AiQuotaUnavailable):
        client.release(RESERVATION, USER)


def test_an_unexpected_client_error_means_unavailable_and_is_not_retried():
    client, upstream, _ = build(httpx.Response(422, json={}))

    with pytest.raises(AiQuotaUnavailable):
        client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 1


def test_the_open_breaker_rejects_calls_without_reaching_entitlement():
    client, upstream, _ = build(httpx.Response(503), attempts=1, threshold=2)
    for _ in range(2):
        with pytest.raises(AiQuotaUnavailable):
            client.reserve(RESERVATION, USER)

    with pytest.raises(AiQuotaUnavailable):
        client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 2


def test_business_answers_do_not_open_the_breaker():
    client, upstream, _ = build(problem(409, "quota_exhausted"), threshold=2)
    for _ in range(4):
        with pytest.raises(AiQuotaExhausted):
            client.reserve(RESERVATION, USER)

    assert len(upstream.requests) == 4


def test_the_correlation_id_is_forwarded():
    set_correlation_id("req-42")
    client, upstream, _ = build(reserved())

    client.reserve(RESERVATION, USER)

    assert upstream.requests[0].headers[CORRELATION_HEADER] == "req-42"


def test_the_tier_is_read_from_the_entitlement_resource():
    client, upstream, _ = build(httpx.Response(200, json={"tier": "hobbyist"}))

    assert client.tier_of(USER) == Tier.HOBBYIST
    assert upstream.requests[0].url.path == f"/v1/entitlements/{USER}"


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(200, json={"tier": "gold"}), httpx.Response(200, json=[]), httpx.Response(404)],
)
def test_an_unusable_tier_answer_means_unavailable(answer: httpx.Response):
    client, _, _ = build(answer)

    with pytest.raises(AiQuotaUnavailable):
        client.tier_of(USER)


def test_calls_are_logged_without_the_user_or_the_body(caplog: pytest.LogCaptureFixture):
    client, _, _ = build(reserved())

    with caplog.at_level("INFO"):
        client.reserve(RESERVATION, USER)

    (record,) = [r for r in caplog.records if r.message == "outgoing call to entitlement"]
    assert (record.target, record.call, record.status) == ("entitlement", "reserve", 201)
    assert str(USER) not in str(record.__dict__)


def test_the_factory_builds_a_client_from_settings():
    client = create_entitlement_client(make_settings(entitlement_url="http://e:1"))

    assert isinstance(client, EntitlementClient)
    client.close()
