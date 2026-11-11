from datetime import timedelta

import pytest

from app.service import escalation_publisher
from app.service.errors import ContextTooLarge, PublishFailed
from app.service.escalation_events import CONTEXT_LIMIT_BYTES, context_size
from app.service.model import DiagnosisStatus, HistoryEntry
from tests.unit.delivery_scenario import (
    ACK_MARGIN,
    HISTORY_LIMIT,
    TTL,
    DeliveryScenario,
)
from tests.unit.fakes import NOW


@pytest.fixture
def scenario() -> DeliveryScenario:
    return DeliveryScenario()


def test_publish_sends_the_event_built_from_the_stored_rows(scenario: DeliveryScenario):
    escalation = scenario.escalate(user_question="Why?")

    scenario.publisher.publish(escalation)

    (event,) = scenario.events.published
    assert event.event_id == escalation.id
    assert event.occurred_at == escalation.created_at
    assert event.accept_until == escalation.created_at + TTL - ACK_MARGIN
    assert event.context["user_question"] == "Why?"
    assert event.context["species"] == "Mint"
    assert context_size(event.context) <= CONTEXT_LIMIT_BYTES


def test_the_history_is_the_earlier_updates_oldest_first_and_limited(scenario: DeliveryScenario):
    delivery = scenario.uow.escalation_delivery
    delivery.history_entries = [
        HistoryEntry(NOW - timedelta(days=day), DiagnosisStatus.COMPLETED, f"c{day}")
        for day in (1, 2, 3)
    ] + [HistoryEntry(NOW + timedelta(days=1), None, "later")]

    scenario.publisher.publish(scenario.escalate())

    (event,) = scenario.events.published
    assert [h["top_cause"] for h in event.context["history"]] == ["c3", "c2", "c1"]
    assert delivery.history_calls == [
        (scenario.journal.id, scenario.update.created_at, HISTORY_LIMIT)
    ]


def test_a_republished_event_is_identical_to_the_first(scenario: DeliveryScenario):
    escalation = scenario.escalate()

    scenario.publisher.publish(escalation)
    scenario.publisher.publish(escalation)

    first, second = scenario.events.published
    assert first == second


def test_publish_failure_propagates(scenario: DeliveryScenario):
    scenario.events.fail = True

    with pytest.raises(PublishFailed):
        scenario.publisher.publish(scenario.escalate())


def test_announce_publishes_and_swallows_broker_failures(
    scenario: DeliveryScenario, caplog: pytest.LogCaptureFixture
):
    escalation = scenario.escalate()
    scenario.events.fail = True

    with caplog.at_level("ERROR"):
        scenario.publisher.announce(escalation)

    assert scenario.events.published == []
    assert "not published" in caplog.text
    scenario.events.fail = False
    scenario.publisher.announce(escalation)
    assert len(scenario.events.published) == 1


def test_announce_swallows_an_oversized_context(
    scenario: DeliveryScenario, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def too_large(source):
        raise ContextTooLarge("too big")

    monkeypatch.setattr(escalation_publisher, "build_context", too_large)

    with caplog.at_level("ERROR"):
        scenario.publisher.announce(scenario.escalate())

    assert scenario.events.published == []
    assert "not published" in caplog.text


def test_an_escalation_whose_update_vanished_is_a_bug_not_an_event(scenario: DeliveryScenario):
    escalation = scenario.escalate()
    scenario.uow.updates.rows.clear()

    with pytest.raises(RuntimeError):
        scenario.publisher.publish(escalation)
