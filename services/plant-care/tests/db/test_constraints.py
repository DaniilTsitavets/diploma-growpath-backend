import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.models import DiagnosisModel, EscalationModel, JournalModel
from tests.db.factories import (
    add_candidate,
    add_diagnosis,
    add_escalation,
    add_journal,
    add_photo,
    add_update,
    violated,
)

ANSWERED = {"status": "answered", "answer_text": "Water less", "answered_at": datetime.now(UTC)}
COMPLETED = {"status": "completed", "model_effort": "high", "recommendation": "Repot"}


def rejected_by(session: Session, constraint: str, add, **fields):
    with violated(session) as violation:
        add(session, **fields)

    assert violation.constraint == constraint


def test_journal_order_is_unique(session: Session):
    order_id = add_journal(session)["order_id"]

    rejected_by(session, "uq_journals_order_id", add_journal, order_id=order_id)


@pytest.mark.parametrize("species", ["  ", "", "x" * 101])
def test_journal_species_must_be_non_blank_and_short(session: Session, species: str):
    rejected_by(session, "ck_journals_species", add_journal, species=species)


def test_journal_accepts_a_hundred_characters_and_starts_unplanted(session: Session):
    values = add_journal(session, species="x" * 100)

    row = session.execute(select(JournalModel).where(JournalModel.id == values["id"])).scalar_one()

    assert row.planted_on is None
    assert row.created_at is not None


@pytest.mark.parametrize(
    ("constraint", "fields"),
    [
        ("ck_updates_lighting", {"lighting": "dark"}),
        ("ck_updates_watering_method", {"watering_method": "sprinkled"}),
        ("ck_updates_pot_diameter_class", {"pot_diameter_class": "huge"}),
        ("ck_updates_drainage_holes", {"drainage_holes": "maybe"}),
        ("ck_updates_standing_water_over_1h", {"standing_water_over_1h": "maybe"}),
        ("ck_updates_dry_depth", {"dry_depth": "damp"}),
        ("ck_updates_pot_weight_relative", {"pot_weight_relative": "heavier"}),
        ("ck_updates_pot_weight_after_watering_g", {"pot_weight_after_watering_g": 0}),
        ("ck_updates_pot_weight_after_watering_g", {"pot_weight_after_watering_g": 200001}),
        ("ck_updates_pot_weight_now_g", {"pot_weight_now_g": 0}),
        ("ck_updates_repotted_or_moved", {"repotted_or_moved": "unknown"}),
        ("ck_updates_sensor_reading", {"sensor_reading": -0.01, "sensor_unit": "raw"}),
        ("ck_updates_sensor_unit", {"sensor_reading": 1, "sensor_unit": "pct"}),
        ("ck_updates_sensor_reading_unit", {"sensor_reading": 1}),
        ("ck_updates_sensor_reading_unit", {"sensor_unit": "raw"}),
        ("ck_updates_sensor_vwc_pct", {"sensor_reading": 100.5, "sensor_unit": "vwc_pct"}),
        ("ck_updates_air_temp_c", {"air_temp_c": 60.5}),
        ("ck_updates_air_temp_min_c", {"air_temp_min_c": -50.5}),
        ("ck_updates_air_temp_max_c", {"air_temp_max_c": 61}),
        ("ck_updates_air_temp_range", {"air_temp_min_c": 20, "air_temp_max_c": 10}),
        ("ck_updates_air_rh_pct", {"air_rh_pct": 100.5}),
        ("ck_updates_air_rh_min_pct", {"air_rh_min_pct": -1}),
        ("ck_updates_air_rh_max_pct", {"air_rh_max_pct": 101}),
        ("ck_updates_air_rh_range", {"air_rh_min_pct": 70, "air_rh_max_pct": 60}),
    ],
)
def test_update_rejects_values_outside_the_questionnaire(
    session: Session, constraint: str, fields: dict
):
    rejected_by(session, constraint, add_update, **fields)


def test_update_needs_an_existing_journal(session: Session):
    rejected_by(session, "fk_updates_journal_id_journals", add_update, journal_id=uuid.uuid4())


def test_update_accepts_the_edges_of_the_questionnaire(session: Session):
    add_update(session, sensor_reading=0, sensor_unit="vwc_pct", pot_weight_now_g=0.01)
    add_update(session, sensor_reading=100, sensor_unit="vwc_pct", pot_weight_now_g=200000)
    add_update(session, sensor_reading=5000.5, sensor_unit="raw")
    add_update(session, air_temp_min_c=-50, air_temp_max_c=-50, air_rh_min_pct=0, air_rh_max_pct=0)
    add_update(
        session, air_temp_min_c=60, air_temp_max_c=60, air_rh_min_pct=100, air_rh_max_pct=100
    )


def test_photo_kind_must_be_documented(session: Session):
    rejected_by(session, "ck_update_photos_kind", add_photo, kind="roots")


@pytest.mark.parametrize("storage_key", ["  ", "k" * 513])
def test_photo_storage_key_must_be_non_blank_and_short(session: Session, storage_key: str):
    rejected_by(session, "ck_update_photos_storage_key", add_photo, storage_key=storage_key)


def test_photo_is_unique_per_update_and_kind(session: Session):
    photo = add_photo(session)

    rejected_by(session, "pk_update_photos", add_photo, update_id=photo["update_id"])


def test_photo_needs_an_existing_update(session: Session):
    rejected_by(session, "fk_update_photos_update_id_updates", add_photo, update_id=uuid.uuid4())


@pytest.mark.parametrize(
    ("constraint", "fields"),
    [
        ("ck_diagnoses_status", {"status": "failed", "model_effort": "high"}),
        ("ck_diagnoses_model_effort", {"model_effort": "low"}),
        ("ck_diagnoses_answer_needs_question", {"clarifying_answer": "yes"}),
        ("ck_diagnoses_awaiting", {"status": "awaiting_clarification", "model_effort": "medium"}),
        ("ck_diagnoses_roots_photo", {"roots_photo_requested": True}),
        (
            "ck_diagnoses_model_effort_set",
            {"status": "completed", "recommendation": "r", "has_dominant_candidate": True},
        ),
        ("ck_diagnoses_no_recommendation_yet", {"recommendation": "r"}),
        ("ck_diagnoses_finished", {**COMPLETED}),
        ("ck_diagnoses_finished", {"status": "degraded", "model_effort": "high"}),
        ("ck_diagnoses_lengths", {"clarifying_question": "q" * 501}),
        ("ck_diagnoses_escalation_offered", {"escalation_offered": True}),
        ("ck_diagnoses_escalation_declined", {"escalation_declined": True}),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_diagnosis_rejects_states_the_design_forbids(
    session: Session, constraint: str, fields: dict
):
    rejected_by(session, constraint, add_diagnosis, **fields)


def test_diagnosis_cannot_offer_an_expert_before_a_clarification_answer(session: Session):
    rejected_by(
        session,
        "ck_diagnoses_escalation_offered",
        add_diagnosis,
        **COMPLETED,
        has_dominant_candidate=False,
        escalation_offered=True,
        clarifying_question="Are the roots brown?",
    )


def test_diagnosis_offers_an_expert_after_an_answer_without_a_dominant_candidate(
    session: Session,
):
    add_diagnosis(
        session,
        **COMPLETED,
        has_dominant_candidate=False,
        escalation_offered=True,
        escalation_declined=True,
        clarifying_question="Are the roots brown?",
        clarifying_answer="Yes",
    )


def test_diagnosis_allows_only_one_clarification_round(session: Session):
    values = add_diagnosis(
        session,
        status="diagnosis_pending",
        model_effort="medium",
        clarifying_question="First?",
        clarifying_answer="Yes",
    )

    with violated(session) as violation:
        session.execute(
            update(DiagnosisModel)
            .where(DiagnosisModel.update_id == values["update_id"])
            .values(status="awaiting_clarification", clarifying_question="Second?")
        )

    assert violation.constraint == "ck_diagnoses_awaiting"


def test_diagnosis_flags_default_to_false(session: Session):
    values = add_diagnosis(session)

    row = session.execute(
        select(DiagnosisModel).where(DiagnosisModel.update_id == values["update_id"])
    ).scalar_one()

    assert (row.roots_photo_requested, row.escalation_offered, row.escalation_declined) == (
        False,
        False,
        False,
    )


def test_diagnosis_flags_are_not_nullable(session: Session):
    with violated(session):
        session.execute(
            insert(DiagnosisModel).values(
                update_id=add_update(session)["id"],
                ai_reservation_id=uuid.uuid4(),
                status="diagnosis_pending",
                escalation_offered=None,
            )
        )


def test_diagnosis_is_one_per_update_and_reservations_are_unique(session: Session):
    first = add_diagnosis(session)

    rejected_by(session, "pk_diagnoses", add_diagnosis, update_id=first["update_id"])
    rejected_by(
        session,
        "uq_diagnoses_ai_reservation_id",
        add_diagnosis,
        ai_reservation_id=first["ai_reservation_id"],
    )


def test_diagnosis_needs_an_existing_update(session: Session):
    rejected_by(session, "fk_diagnoses_update_id_updates", add_diagnosis, update_id=uuid.uuid4())


@pytest.mark.parametrize(
    ("constraint", "fields"),
    [
        ("ck_diagnosis_candidates_rank", {"rank": 0}),
        ("ck_diagnosis_candidates_lengths", {"cause": "c" * 201}),
        ("ck_diagnosis_candidates_lengths", {"rationale": "r" * 1001}),
    ],
)
def test_candidate_rejects_bad_rank_and_lengths(session: Session, constraint: str, fields: dict):
    update_id = add_diagnosis(session)["update_id"]

    rejected_by(session, constraint, add_candidate, update_id=update_id, **fields)


def test_candidate_needs_a_diagnosis_and_a_unique_rank(session: Session):
    rejected_by(
        session,
        "fk_diagnosis_candidates_update_id_diagnoses",
        add_candidate,
        update_id=add_update(session)["id"],
    )
    update_id = add_diagnosis(session)["update_id"]
    add_candidate(session, update_id)

    rejected_by(session, "pk_diagnosis_candidates", add_candidate, update_id=update_id)


@pytest.mark.parametrize(
    ("constraint", "fields"),
    [
        ("ck_escalations_status", {"status": "expired"}),
        ("ck_escalations_ticket_open", {"status": "ticket_open"}),
        ("ck_escalations_answered", {"status": "answered", "ticket_id": uuid.uuid4()}),
        ("ck_escalations_answered", {**ANSWERED, "status": "requested", "ticket_id": uuid.uuid4()}),
        ("ck_escalations_answered", {**ANSWERED}),
        (
            "ck_escalations_lengths",
            {**ANSWERED, "ticket_id": uuid.uuid4(), "answer_text": "x" * 5001},
        ),
        ("ck_escalations_lengths", {"user_question": "q" * 1001}),
    ],
)
def test_escalation_rejects_states_the_design_forbids(
    session: Session, constraint: str, fields: dict
):
    rejected_by(session, constraint, add_escalation, **fields)


def test_escalation_answered_requires_the_answer_the_ticket_and_the_time(session: Session):
    add_escalation(session, **ANSWERED, ticket_id=uuid.uuid4())


def test_escalation_ticket_id_is_unique(session: Session):
    ticket_id = add_escalation(session, status="ticket_open", ticket_id=uuid.uuid4())["ticket_id"]

    rejected_by(session, "uq_escalations_ticket_id", add_escalation, ticket_id=ticket_id)


def test_update_has_one_active_escalation_and_may_be_escalated_again_after_a_cancellation(
    session: Session,
):
    update_id = add_update(session)["id"]
    first = add_escalation(session, update_id)

    rejected_by(session, "uq_escalations_active_update", add_escalation, update_id=update_id)

    session.execute(
        update(EscalationModel).where(EscalationModel.id == first["id"]).values(status="cancelled")
    )
    add_escalation(session, update_id)


def test_cancelled_escalations_of_one_update_may_repeat(session: Session):
    update_id = add_update(session)["id"]

    add_escalation(session, update_id, status="cancelled")
    add_escalation(session, update_id, status="cancelled")


def test_escalation_needs_an_existing_update(session: Session):
    rejected_by(session, "fk_escalations_update_id_updates", add_escalation, update_id=uuid.uuid4())
