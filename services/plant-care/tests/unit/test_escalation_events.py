import json
from datetime import timedelta

import pytest

from app.service.errors import ContextTooLarge
from app.service.escalation_events import (
    CONTEXT_LIMIT_BYTES,
    ContextSource,
    build_context,
    context_size,
    escalation_requested,
)
from app.service.model import (
    CandidateRecord,
    DiagnosisStatus,
    HistoryEntry,
)
from tests.unit.delivery_scenario import ACK_MARGIN, TTL, DeliveryScenario
from tests.unit.fakes import NOW


def source(scenario: DeliveryScenario, history=(), **escalation) -> ContextSource:
    return ContextSource(
        escalation=scenario.escalate(**escalation),
        species="Mint",
        update=scenario.update,
        photos=scenario.uow.photos.list_for_update(scenario.update.id),
        diagnosis=scenario.uow.diagnoses.get(scenario.update.id),
        history=list(history),
    )


def entry(day: int, cause: str = "Overwatering") -> HistoryEntry:
    return HistoryEntry(NOW - timedelta(days=day), DiagnosisStatus.COMPLETED, cause)


def test_the_context_carries_the_question_the_clarification_and_the_update():
    scenario = DeliveryScenario()

    context = build_context(source(scenario, user_question="Why the spots?"))

    assert context["schema_version"] == 1
    assert context["species"] == "Mint"
    assert context["user_question"] == "Why the spots?"
    assert context["clarifying_question"] == "Is the soil soggy?"
    assert context["clarifying_answer"] == "Yes"
    assert context["update"]["drainage"] == "good"
    assert context["photos"] == [
        {"kind": "overview", "storage_key": "journals/overview"},
        {"kind": "soil", "storage_key": "journals/soil"},
    ]
    assert context["diagnosis"]["status"] == "completed"
    assert context["history"] == []


def test_optional_parts_are_left_out_when_absent():
    scenario = DeliveryScenario()
    scenario.uow.diagnoses.seed(scenario.update.id)

    context = build_context(source(scenario))

    assert "user_question" not in context
    assert "clarifying_question" not in context
    assert "clarifying_answer" not in context


def test_without_a_diagnosis_the_context_has_no_diagnosis():
    scenario = DeliveryScenario()
    escalation = source(scenario)

    context = build_context(
        ContextSource(
            escalation.escalation,
            "Mint",
            escalation.update,
            [],
            None,
            [],
        )
    )

    assert "diagnosis" not in context
    assert context["photos"] == []


def test_the_candidates_are_ranked_in_the_context():
    scenario = DeliveryScenario()
    scenario.uow.diagnoses.seed(
        scenario.update.id,
        candidates=[CandidateRecord(1, "Rot", "soft"), CandidateRecord(2, "Mite", "dots")],
    )

    context = build_context(source(scenario))

    assert [c["cause"] for c in context["diagnosis"]["candidates"]] == ["Rot", "Mite"]


def test_the_history_holds_dates_and_outcomes_and_no_photos():
    scenario = DeliveryScenario()

    context = build_context(source(scenario, history=[entry(3), entry(1, "Root rot")]))

    assert context["history"] == [
        {"date": "2026-11-08", "diagnosis_status": "completed", "top_cause": "Overwatering"},
        {"date": "2026-11-10", "diagnosis_status": "completed", "top_cause": "Root rot"},
    ]


def test_an_update_without_a_diagnosis_is_a_history_entry_with_no_outcome():
    scenario = DeliveryScenario()

    context = build_context(source(scenario, history=[HistoryEntry(NOW, None, None)]))

    assert context["history"] == [
        {"date": "2026-11-11", "diagnosis_status": None, "top_cause": None}
    ]


def test_the_size_is_measured_in_utf8_bytes_of_the_compact_json():
    context = {"text": "жук"}

    compact = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    assert compact == '{"text":"жук"}'
    assert context_size(context) == len(compact) + 3


def test_an_oversized_history_loses_its_oldest_entries_first():
    scenario = DeliveryScenario()
    cause = "н" * 200
    history = [entry(day, f"{day:03d}{cause}") for day in range(300, 0, -1)]

    context = build_context(source(scenario, history=history))

    kept = context["history"]
    assert 0 < len(kept) < len(history)
    assert context_size(context) <= CONTEXT_LIMIT_BYTES
    assert kept[-1]["top_cause"].startswith("001")
    assert [h["top_cause"] for h in kept] == [e.top_cause for e in history[-len(kept) :]]


def test_multibyte_text_counts_bytes_not_characters():
    scenario = DeliveryScenario()
    history = [entry(day, "蟲" * 200) for day in range(150, 0, -1)]
    characters = sum(len(e.top_cause) for e in history)

    context = build_context(source(scenario, history=history))

    kept_characters = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    assert characters * 3 > CONTEXT_LIMIT_BYTES > characters
    assert kept_characters < context_size(context) <= CONTEXT_LIMIT_BYTES
    assert len(context["history"]) < len(history)


def test_a_context_over_the_limit_without_history_is_an_error():
    scenario = DeliveryScenario()

    with pytest.raises(ContextTooLarge):
        build_context(source(scenario, history=[entry(1)]), max_bytes=100)


def test_the_event_is_built_from_the_row_and_the_configuration():
    scenario = DeliveryScenario()
    escalation = scenario.escalate()

    event = escalation_requested(escalation, {"schema_version": 1}, TTL, ACK_MARGIN)

    assert event.event_id == event.escalation_id == escalation.id
    assert event.occurred_at == escalation.created_at
    assert event.accept_until == escalation.created_at + TTL - ACK_MARGIN
    assert (event.user_id, event.journal_id, event.update_id) == (
        escalation.user_id,
        escalation.journal_id,
        escalation.update_id,
    )
