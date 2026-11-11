import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiagnosisCandidateModel, DiagnosisModel
from app.repository.diagnoses import SqlAlchemyDiagnosisRepository
from app.service.model import Candidate, DiagnosisResult, DiagnosisStatus, ModelEffort
from tests.db.factories import add_candidate, add_diagnosis, add_update, violated

RESERVATION = uuid.uuid4()
OTHER = uuid.uuid4()


def result(**overrides) -> DiagnosisResult:
    fields = {
        "status": DiagnosisStatus.COMPLETED,
        "has_dominant_candidate": True,
        "candidates": [Candidate("Overwatering", "Wet soil"), Candidate("Drainage", "Heavy")],
        "recommendation": "Water less.",
        "clarifying_question": None,
        "roots_photo_requested": False,
        "escalation_offered": False,
    }
    return DiagnosisResult(**{**fields, **overrides})


def awaiting(**overrides) -> DiagnosisResult:
    return result(
        status=DiagnosisStatus.AWAITING_CLARIFICATION,
        has_dominant_candidate=False,
        recommendation=None,
        clarifying_question="Soft leaves?",
        roots_photo_requested=True,
        **overrides,
    )


def stored(session: Session, update_id: uuid.UUID) -> DiagnosisModel:
    return session.scalars(
        select(DiagnosisModel)
        .where(DiagnosisModel.update_id == update_id)
        .execution_options(populate_existing=True)
    ).one()


def candidate_causes(session: Session, update_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(DiagnosisCandidateModel.cause)
            .where(DiagnosisCandidateModel.update_id == update_id)
            .order_by(DiagnosisCandidateModel.rank)
        )
    )


def pending(session: Session, **overrides) -> uuid.UUID:
    update_id = add_update(session)["id"]
    add_diagnosis(session, update_id, ai_reservation_id=RESERVATION, **overrides)
    return update_id


def test_a_diagnosis_is_read_with_its_ranked_candidates(session: Session):
    update_id = pending(session)
    add_candidate(session, update_id, rank=2, cause="Second")
    add_candidate(session, update_id, rank=1, cause="First")

    diagnosis = SqlAlchemyDiagnosisRepository(session).get(update_id)

    assert diagnosis.ai_reservation_id == RESERVATION
    assert diagnosis.summary.status is DiagnosisStatus.PENDING
    assert [(c.rank, c.cause) for c in diagnosis.candidates] == [(1, "First"), (2, "Second")]


def test_an_unknown_diagnosis_is_none(session: Session):
    assert SqlAlchemyDiagnosisRepository(session).get(uuid.uuid4()) is None


def test_the_first_attempt_claims_an_unstarted_diagnosis_once(session: Session):
    update_id = pending(session)
    repository = SqlAlchemyDiagnosisRepository(session)

    first = repository.claim_first_attempt(update_id, RESERVATION, ModelEffort.HIGH)
    second = repository.claim_first_attempt(update_id, RESERVATION, ModelEffort.MEDIUM)

    assert (first, second) == (True, False)
    assert stored(session, update_id).model_effort == "high"


def test_the_first_attempt_needs_the_current_reservation(session: Session):
    update_id = pending(session)

    claimed = SqlAlchemyDiagnosisRepository(session).claim_first_attempt(
        update_id, OTHER, ModelEffort.HIGH
    )

    assert claimed is False
    assert stored(session, update_id).model_effort is None


def test_a_retry_replaces_the_reservation_and_resets_a_degraded_diagnosis(session: Session):
    update_id = pending(
        session, status="degraded", model_effort="medium", recommendation="fallback"
    )
    repository = SqlAlchemyDiagnosisRepository(session)

    claimed = repository.claim_retry(update_id, RESERVATION, OTHER, ModelEffort.HIGH)

    row = stored(session, update_id)
    assert claimed is True
    assert (row.ai_reservation_id, row.status, row.model_effort) == (
        OTHER,
        "diagnosis_pending",
        "high",
    )
    assert row.recommendation is None


def test_a_retry_cannot_use_a_stale_reservation_or_a_finished_diagnosis(session: Session):
    degraded = pending(session, status="degraded", model_effort="high", recommendation="r")
    done = add_update(session)["id"]
    add_diagnosis(
        session,
        done,
        status="completed",
        model_effort="high",
        has_dominant_candidate=True,
        recommendation="r",
    )
    repository = SqlAlchemyDiagnosisRepository(session)

    assert repository.claim_retry(degraded, OTHER, uuid.uuid4(), ModelEffort.HIGH) is False
    assert repository.claim_retry(done, RESERVATION, uuid.uuid4(), ModelEffort.HIGH) is False


def test_the_answer_moves_an_awaiting_diagnosis_to_pending_once(session: Session):
    update_id = pending(
        session, status="awaiting_clarification", model_effort="high", clarifying_question="Q?"
    )
    repository = SqlAlchemyDiagnosisRepository(session)

    first = repository.answer_clarification(update_id, "yes")
    second = repository.answer_clarification(update_id, "again")

    row = stored(session, update_id)
    assert (first, second) == (True, False)
    assert (row.status, row.clarifying_answer) == ("diagnosis_pending", "yes")
    assert row.ai_reservation_id == RESERVATION


def test_an_answer_needs_the_diagnosis_to_await_one(session: Session):
    update_id = pending(session)

    assert SqlAlchemyDiagnosisRepository(session).answer_clarification(update_id, "x") is False


def test_a_completed_result_replaces_the_candidates(session: Session):
    update_id = pending(session, model_effort="high")
    add_candidate(session, update_id, rank=1, cause="Old one")
    add_candidate(session, update_id, rank=2, cause="Old two")
    add_candidate(session, update_id, rank=3, cause="Old three")

    recorded = SqlAlchemyDiagnosisRepository(session).record_result(
        update_id, RESERVATION, result()
    )

    row = stored(session, update_id)
    assert recorded is True
    assert (row.status, row.has_dominant_candidate, row.recommendation) == (
        "completed",
        True,
        "Water less.",
    )
    assert candidate_causes(session, update_id) == ["Overwatering", "Drainage"]


def test_a_question_is_stored_with_the_roots_photo_request(session: Session):
    update_id = pending(session, model_effort="high")

    SqlAlchemyDiagnosisRepository(session).record_result(update_id, RESERVATION, awaiting())

    row = stored(session, update_id)
    assert (row.status, row.clarifying_question, row.roots_photo_requested) == (
        "awaiting_clarification",
        "Soft leaves?",
        True,
    )
    assert row.recommendation is None


def test_the_conclusion_after_an_answer_keeps_the_question_and_offers_the_expert(
    session: Session,
):
    update_id = pending(
        session,
        model_effort="high",
        clarifying_question="Soft leaves?",
        clarifying_answer="Yes",
        roots_photo_requested=True,
    )

    SqlAlchemyDiagnosisRepository(session).record_result(
        update_id,
        RESERVATION,
        result(has_dominant_candidate=False, escalation_offered=True),
    )

    row = stored(session, update_id)
    assert (row.clarifying_question, row.clarifying_answer) == ("Soft leaves?", "Yes")
    assert row.roots_photo_requested is True
    assert (row.status, row.escalation_offered) == ("completed", True)


def test_a_result_of_a_lost_attempt_changes_nothing(session: Session):
    update_id = pending(session, model_effort="high")
    add_candidate(session, update_id, rank=1, cause="Winner's")

    recorded = SqlAlchemyDiagnosisRepository(session).record_result(update_id, OTHER, result())

    assert recorded is False
    assert stored(session, update_id).status == "diagnosis_pending"
    assert candidate_causes(session, update_id) == ["Winner's"]


def test_a_result_needs_a_pending_diagnosis(session: Session):
    update_id = pending(session, status="degraded", model_effort="high", recommendation="r")

    assert (
        SqlAlchemyDiagnosisRepository(session).record_result(update_id, RESERVATION, result())
        is False
    )


def test_the_database_refuses_an_expert_offer_without_a_clarification(session: Session):
    update_id = pending(session, model_effort="high")

    with violated(session) as violation:
        SqlAlchemyDiagnosisRepository(session).record_result(
            update_id,
            RESERVATION,
            result(has_dominant_candidate=False, escalation_offered=True),
        )

    assert violation.constraint == "ck_diagnoses_escalation_offered"


def test_the_database_refuses_an_expert_offer_next_to_a_dominant_candidate(session: Session):
    update_id = pending(
        session, model_effort="high", clarifying_question="Q?", clarifying_answer="A"
    )

    with violated(session) as violation:
        SqlAlchemyDiagnosisRepository(session).record_result(
            update_id, RESERVATION, result(has_dominant_candidate=True, escalation_offered=True)
        )

    assert violation.constraint == "ck_diagnoses_escalation_offered"


def test_a_degraded_attempt_stores_the_fallback_once(session: Session):
    update_id = pending(session, model_effort="high")
    repository = SqlAlchemyDiagnosisRepository(session)

    first = repository.mark_degraded(update_id, RESERVATION, "fallback")
    second = repository.mark_degraded(update_id, RESERVATION, "fallback")

    row = stored(session, update_id)
    assert (first, second) == (True, False)
    assert (row.status, row.recommendation) == ("degraded", "fallback")


def test_a_degraded_mark_of_a_lost_attempt_changes_nothing(session: Session):
    update_id = pending(session, model_effort="high")

    assert SqlAlchemyDiagnosisRepository(session).mark_degraded(update_id, OTHER, "x") is False
    assert stored(session, update_id).status == "diagnosis_pending"


def test_declining_needs_an_offered_expert_and_happens_once(session: Session):
    offered = pending(
        session,
        status="completed",
        model_effort="high",
        has_dominant_candidate=False,
        recommendation="r",
        clarifying_question="Q?",
        clarifying_answer="A",
        escalation_offered=True,
    )
    not_offered = add_update(session)["id"]
    add_diagnosis(session, not_offered)
    repository = SqlAlchemyDiagnosisRepository(session)

    assert repository.decline_escalation(not_offered) is False
    assert repository.decline_escalation(offered) is True
    assert repository.decline_escalation(offered) is False
    assert stored(session, offered).escalation_declined is True
