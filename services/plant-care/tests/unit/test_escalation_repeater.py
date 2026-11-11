from datetime import timedelta

import pytest

from app.service.errors import ExpertQuotaUnavailable, ExpertReservationCommitted
from app.service.model import EscalationStatus
from tests.unit.delivery_scenario import BATCH, GRACE, TTL, DeliveryScenario

MINUTE = timedelta(minutes=1)


@pytest.fixture
def scenario() -> DeliveryScenario:
    return DeliveryScenario()


def status(scenario: DeliveryScenario, escalation_id) -> EscalationStatus:
    return scenario.uow.escalations.rows[escalation_id].status


def test_a_requested_escalation_past_the_grace_is_republished(scenario: DeliveryScenario):
    due = scenario.at(GRACE + MINUTE)

    sent = scenario.repeater.republish_due()

    assert sent == 1
    (event,) = scenario.events.published
    assert event.escalation_id == due.id


def test_a_fresh_escalation_is_left_to_the_request_path(scenario: DeliveryScenario):
    scenario.at(GRACE - timedelta(seconds=1))

    assert scenario.repeater.republish_due() == 0
    assert scenario.events.published == []


@pytest.mark.parametrize(
    "status_",
    [EscalationStatus.TICKET_OPEN, EscalationStatus.ANSWERED, EscalationStatus.CANCELLED],
    ids=lambda s: s.value,
)
def test_only_requested_escalations_are_republished(
    scenario: DeliveryScenario, status_: EscalationStatus
):
    scenario.at(MINUTE * 5, status=status_)

    assert scenario.repeater.republish_due() == 0


def test_an_escalation_at_the_ttl_is_no_longer_republished(scenario: DeliveryScenario):
    scenario.at(TTL)

    assert scenario.repeater.republish_due() == 0


def test_the_batch_size_limits_one_pass_and_the_oldest_go_first(scenario: DeliveryScenario):
    escalations = [scenario.at(GRACE + MINUTE * (BATCH + 5 - i)) for i in range(BATCH + 3)]

    sent = scenario.repeater.republish_due()

    assert sent == BATCH
    assert scenario.uow.escalation_delivery.claim_limits == [BATCH]
    assert [e.escalation_id for e in scenario.events.published] == [
        e.id for e in escalations[:BATCH]
    ]


def test_a_failed_publish_is_logged_and_the_rest_still_go_out(scenario: DeliveryScenario):
    first = scenario.at(MINUTE * 3)
    second = scenario.at(MINUTE * 2)
    publish = scenario.events.publish_escalation_requested

    def flaky(event):
        scenario.events.fail = event.escalation_id == first.id
        publish(event)

    scenario.events.publish_escalation_requested = flaky

    assert scenario.repeater.republish_due() == 1
    assert [e.escalation_id for e in scenario.events.published] == [second.id]


def test_the_stale_escalation_releases_the_reservation_before_it_is_cancelled(
    scenario: DeliveryScenario,
):
    stale = scenario.at(TTL)
    seen: list[EscalationStatus] = []
    release = scenario.quota.release_expert

    def spy(escalation_id, user_id):
        seen.append(status(scenario, escalation_id))
        release(escalation_id, user_id)

    scenario.quota.release_expert = spy

    cancelled = scenario.repeater.expire_stale()

    assert cancelled == 1
    assert seen == [EscalationStatus.REQUESTED]
    assert scenario.quota.released == [stale.id]
    assert status(scenario, stale.id) is EscalationStatus.CANCELLED
    assert scenario.uow.commits == 1


def test_an_escalation_younger_than_the_ttl_is_not_expired(scenario: DeliveryScenario):
    young = scenario.at(TTL - MINUTE)

    assert scenario.repeater.expire_stale() == 0
    assert scenario.quota.log == []
    assert status(scenario, young.id) is EscalationStatus.REQUESTED


def test_a_committed_reservation_keeps_the_escalation_requested(scenario: DeliveryScenario):
    stale = scenario.at(TTL + MINUTE)
    scenario.quota.release_fails_with = ExpertReservationCommitted()

    assert scenario.repeater.expire_stale() == 0
    assert status(scenario, stale.id) is EscalationStatus.REQUESTED


def test_an_unavailable_quota_service_keeps_the_escalation_for_the_next_pass(
    scenario: DeliveryScenario,
):
    stale = scenario.at(TTL + MINUTE)
    scenario.quota.release_fails_with = ExpertQuotaUnavailable()

    assert scenario.repeater.expire_stale() == 0
    assert status(scenario, stale.id) is EscalationStatus.REQUESTED

    scenario.quota.release_fails_with = None
    assert scenario.repeater.expire_stale() == 1
    assert status(scenario, stale.id) is EscalationStatus.CANCELLED


def test_one_stale_escalation_does_not_block_the_next(scenario: DeliveryScenario):
    committed = scenario.at(TTL + MINUTE * 2)
    abandoned = scenario.at(TTL + MINUTE)
    release = scenario.quota.release_expert

    def selective(escalation_id, user_id):
        if escalation_id == committed.id:
            raise ExpertReservationCommitted
        release(escalation_id, user_id)

    scenario.quota.release_expert = selective

    assert scenario.repeater.expire_stale() == 1
    assert status(scenario, committed.id) is EscalationStatus.REQUESTED
    assert status(scenario, abandoned.id) is EscalationStatus.CANCELLED


def test_a_ticket_event_that_won_the_race_is_not_overwritten_by_the_cancel(
    scenario: DeliveryScenario,
):
    stale = scenario.at(TTL + MINUTE)
    release = scenario.quota.release_expert

    def ticket_arrives_meanwhile(escalation_id, user_id):
        release(escalation_id, user_id)
        scenario.uow.escalations.change(escalation_id, status=EscalationStatus.TICKET_OPEN)

    scenario.quota.release_expert = ticket_arrives_meanwhile

    assert scenario.repeater.expire_stale() == 0
    assert status(scenario, stale.id) is EscalationStatus.TICKET_OPEN


def test_run_once_republishes_and_expires_in_one_tick(scenario: DeliveryScenario):
    scenario.at(MINUTE * 5)
    stale = scenario.at(TTL + MINUTE)

    scenario.repeater.run_once()

    assert len(scenario.events.published) == 1
    assert status(scenario, stale.id) is EscalationStatus.CANCELLED


def test_a_cancelled_escalation_frees_the_update_for_a_new_request(scenario: DeliveryScenario):
    stale = scenario.at(TTL + MINUTE)

    scenario.repeater.expire_stale()

    assert scenario.uow.escalations.get_active(stale.update_id) is None
