import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

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
    Answer,
    CandidateRecord,
    DiagnosisStatus,
    DryDepth,
    HistoryEntry,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    WateringMethod,
    YesNo,
)
from tests.unit.delivery_scenario import ACK_MARGIN, TTL, DeliveryScenario
from tests.unit.fakes import NOW

WATERING = {
    "depletion_D": 0.15,
    "D_source": "sensor",
    "target_p": 0.5,
    "p_source": "profile",
    "I_rec_days": 9.3,
    "I_rec_bounds": {"lower_days": 7.1, "upper_days": 14.0, "text": "between 7.1 and 14 days"},
    "I_fact_days": 2,
    "ratio_r": 0.22,
    "Ks": 1.0,
    "status": "overwatering",
    "flags": ["drainage_none"],
    "air": {"T": 22.4, "RH": 48.0, "VPD_kPa": 1.38},
    "calibration": {"n_observations": 2, "reset_reason": None},
    "assumptions": ["The target depletion equals the species dryness threshold."],
    "reliability": "medium",
}


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

    assert context["schema_version"] == 2
    assert context["species"] == "Mint"
    assert context["user_question"] == "Why the spots?"
    assert context["clarifying_question"] == "Is the soil soggy?"
    assert context["clarifying_answer"] == "Yes"
    assert context["update"]["lighting"] == "low"
    assert context["photos"] == [
        {"kind": "overview", "storage_key": "journals/overview"},
        {"kind": "soil", "storage_key": "journals/soil"},
    ]
    assert context["diagnosis"]["status"] == "completed"
    assert context["history"] == []


def test_the_update_object_carries_the_extended_questionnaire():
    scenario = DeliveryScenario()
    update = replace(
        scenario.update,
        last_watered_on=date(2026, 11, 9),
        watering_method=WateringMethod.THROUGH_DRAINAGE,
        pot_diameter_class=PotDiameterClass.GT_30,
        drainage_holes=Answer.YES,
        standing_water_over_1h=Answer.NO,
        dry_depth=DryDepth.NOT_DRY,
        pot_weight_relative=PotWeightRelative.ABOUT_SAME,
        pot_weight_after_watering_g=Decimal("900.00"),
        pot_weight_now_g=Decimal("850.50"),
        repotted_or_moved=YesNo.NO,
        sensor_reading=Decimal("42.00"),
        sensor_unit=SensorUnit.RAW,
        air_temp_c=Decimal("21.50"),
        air_rh_pct=Decimal("55.00"),
        air_measured_at=NOW,
        air_temp_min_c=Decimal("18.00"),
        air_temp_max_c=Decimal("24.00"),
        air_rh_min_pct=Decimal("40.00"),
        air_rh_max_pct=Decimal("60.00"),
    )

    context = build_context(replace(source(scenario), update=update))

    assert context["update"] == {
        "id": str(update.id),
        "created_at": update.created_at.isoformat(),
        "lighting": "low",
        "last_watered_on": "2026-11-09",
        "watering_method": "through_drainage",
        "pot_diameter_class": "gt_30",
        "drainage_holes": "yes",
        "standing_water_over_1h": "no",
        "dry_depth": "not_dry",
        "pot_weight_relative": "about_same",
        "pot_weight_after_watering_g": "900.00",
        "pot_weight_now_g": "850.50",
        "repotted_or_moved": "no",
        "sensor_reading": "42.00",
        "sensor_unit": "raw",
        "air_temp_c": "21.50",
        "air_rh_pct": "55.00",
        "air_measured_at": NOW.isoformat(),
        "air_temp_min_c": "18.00",
        "air_temp_max_c": "24.00",
        "air_rh_min_pct": "40.00",
        "air_rh_max_pct": "60.00",
    }


def test_unanswered_questions_are_unknown_or_null_in_the_context():
    scenario = DeliveryScenario()

    update = build_context(source(scenario))["update"]

    assert update == {
        "id": str(scenario.update.id),
        "created_at": scenario.update.created_at.isoformat(),
        "lighting": "low",
        "watering_method": "unknown",
        "pot_diameter_class": "unknown",
        "drainage_holes": "unknown",
        "standing_water_over_1h": "unknown",
        "dry_depth": "unknown",
        "pot_weight_relative": "unknown",
    }


def test_optional_parts_are_left_out_when_absent():
    scenario = DeliveryScenario()
    scenario.uow.diagnoses.seed(scenario.update.id)

    context = build_context(source(scenario))

    assert "user_question" not in context
    assert "clarifying_question" not in context
    assert "clarifying_answer" not in context
    assert "watering" not in context


def test_the_stored_watering_assessment_is_carried_untouched():
    scenario = DeliveryScenario()
    scenario.uow.diagnoses.seed(scenario.update.id, watering=WATERING)

    context = build_context(source(scenario))

    assert context["watering"] == WATERING


def test_the_watering_assessment_survives_when_the_history_is_cut():
    scenario = DeliveryScenario()
    scenario.uow.diagnoses.seed(scenario.update.id, watering=WATERING)
    history = [entry(day, "н" * 200) for day in range(300, 0, -1)]

    context = build_context(source(scenario, history=history))

    assert len(context["history"]) < len(history)
    assert context["watering"] == WATERING
    assert context_size(context) <= CONTEXT_LIMIT_BYTES


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
