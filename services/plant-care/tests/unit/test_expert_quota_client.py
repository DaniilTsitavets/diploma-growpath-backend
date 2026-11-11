import json
import uuid

import httpx
import pytest
from growpath_observability import CORRELATION_HEADER, set_correlation_id

from app.service.errors import (
    AiQuotaUnavailable,
    ExpertQuotaExhausted,
    ExpertQuotaUnavailable,
    ExpertReservationCommitted,
)
from tests.unit.test_entitlement_client import build, problem

ESCALATION = uuid.uuid4()
USER = uuid.uuid4()
BASE = "/v1/quotas/expert/reservations"


def reserved(status: int = 201) -> httpx.Response:
    return httpx.Response(
        status, json={"escalation_id": str(ESCALATION), "status": "reserved", "expires_at": "x"}
    )


def test_reserve_posts_the_escalation_id_and_the_owner():
    client, upstream, _ = build(reserved())

    client.reserve_expert(ESCALATION, USER)

    (request,) = upstream.requests
    assert (request.method, request.url.path) == ("POST", BASE)
    assert json.loads(request.content) == {"escalation_id": str(ESCALATION), "user_id": str(USER)}


def test_a_replayed_reservation_answering_200_is_a_success():
    client, _, _ = build(reserved(200))

    client.reserve_expert(ESCALATION, USER)


def test_an_exhausted_quota_maps_to_the_domain_error():
    client, _, _ = build(problem(409, "quota_exhausted"))

    with pytest.raises(ExpertQuotaExhausted):
        client.reserve_expert(ESCALATION, USER)


@pytest.mark.parametrize("code", ["reservation_unusable", "reservation_id_in_use"])
def test_another_conflict_on_reserve_is_not_a_quota_answer(code: str):
    client, upstream, _ = build(problem(409, code))

    with pytest.raises(ExpertQuotaUnavailable):
        client.reserve_expert(ESCALATION, USER)

    assert len(upstream.requests) == 1


@pytest.mark.parametrize("answer", [httpx.Response(422, json={}), httpx.Response(404)])
def test_an_unexpected_answer_on_reserve_means_unavailable(answer: httpx.Response):
    client, _, _ = build(answer)

    with pytest.raises(ExpertQuotaUnavailable):
        client.reserve_expert(ESCALATION, USER)


@pytest.mark.parametrize("status", [200, 404])
def test_release_succeeds_for_a_released_or_unknown_reservation(status: int):
    client, upstream, _ = build(httpx.Response(status, json={}))

    client.release_expert(ESCALATION, USER)

    (request,) = upstream.requests
    assert request.url.path == f"{BASE}/{ESCALATION}/release"
    assert json.loads(request.content) == {"user_id": str(USER)}


def test_a_conflict_on_release_means_the_reservation_is_committed():
    client, _, _ = build(problem(409, "reservation_unusable"))

    with pytest.raises(ExpertReservationCommitted):
        client.release_expert(ESCALATION, USER)


def test_an_unexpected_answer_on_release_means_unavailable():
    client, _, _ = build(httpx.Response(422, json={}))

    with pytest.raises(ExpertQuotaUnavailable):
        client.release_expert(ESCALATION, USER)


def test_a_503_is_retried_and_then_succeeds():
    client, upstream, sleeps = build(httpx.Response(503), reserved())

    client.reserve_expert(ESCALATION, USER)

    assert len(upstream.requests) == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize("call", ["reserve_expert", "release_expert"])
def test_a_timeout_on_every_attempt_means_unavailable(call: str):
    client, upstream, _ = build(httpx.ReadTimeout("slow"), attempts=3)

    with pytest.raises(ExpertQuotaUnavailable):
        getattr(client, call)(ESCALATION, USER)

    assert len(upstream.requests) == 3


def test_the_open_breaker_rejects_expert_calls_without_reaching_entitlement():
    client, upstream, _ = build(httpx.Response(503), attempts=1, threshold=2)
    for _ in range(2):
        with pytest.raises(ExpertQuotaUnavailable):
            client.reserve_expert(ESCALATION, USER)

    with pytest.raises(ExpertQuotaUnavailable):
        client.release_expert(ESCALATION, USER)

    assert len(upstream.requests) == 2


def test_the_breaker_is_shared_with_the_ai_calls():
    client, _, _ = build(httpx.Response(503), attempts=1, threshold=1)
    with pytest.raises(AiQuotaUnavailable):
        client.reserve(ESCALATION, USER)

    with pytest.raises(ExpertQuotaUnavailable):
        client.reserve_expert(ESCALATION, USER)


def test_the_correlation_id_is_forwarded():
    set_correlation_id("req-77")
    client, upstream, _ = build(reserved())

    client.reserve_expert(ESCALATION, USER)

    assert upstream.requests[0].headers[CORRELATION_HEADER] == "req-77"
