import logging
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.service.ai_quota import AiQuota
from app.service.consensus import decide
from app.service.diagnosis_model import DiagnosisModel, DiagnosisRequest, ModelFailure
from app.service.diagnosis_prompt import ANSWER_LIMIT, FALLBACK_RECOMMENDATION
from app.service.diagnosis_samples import collect_answers
from app.service.dominance import DominanceRule
from app.service.effort import EffortPolicy
from app.service.errors import (
    AiQuotaUnavailable,
    DiagnosisConflict,
    DiagnosisNotFound,
    InvalidRequest,
    PhotosMissing,
    ReservationUnusable,
    UpdateNotFound,
)
from app.service.model import (
    Diagnosis,
    DiagnosisResult,
    DiagnosisStatus,
    ModelEffort,
    Photo,
    UpdateRecord,
)
from app.service.photos import missing_required
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attempt:
    """One run of the model for a diagnosis and the reservation it answers to."""

    reservation_id: uuid.UUID
    effort: ModelEffort
    paid: bool
    """False for a re-evaluation, which reuses a reservation that is already committed."""


@dataclass(frozen=True)
class Context:
    update: UpdateRecord
    species: str
    photos: list[Photo]
    diagnosis: Diagnosis


def validate_clarification_answer(text: str) -> str:
    answer = unicodedata.normalize("NFC", text).strip()
    if not 1 <= len(answer) <= ANSWER_LIMIT:
        raise InvalidRequest(f"the answer must have 1 to {ANSWER_LIMIT} characters")
    if any(unicodedata.category(char) == "Cc" and char != "\n" for char in answer):
        raise InvalidRequest("the answer contains control characters")
    return answer


class DiagnosisService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        ai_quota: AiQuota,
        model: DiagnosisModel,
        effort_policy: EffortPolicy,
        dominance_rule: DominanceRule,
        samples: int,
        sample_temperature: float,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._ai_quota = ai_quota
        self._model = model
        self._effort_policy = effort_policy
        self._dominance_rule = dominance_rule
        self._samples = samples
        self._sample_temperature = sample_temperature

    def get(self, user_id: uuid.UUID, update_id: uuid.UUID) -> Diagnosis:
        with self._unit_of_work() as uow:
            return self._load(uow, user_id, update_id).diagnosis

    def run(self, user_id: uuid.UUID, update_id: uuid.UUID) -> Diagnosis:
        """Start or retry the diagnosis; a completed or waiting one is returned unchanged."""
        with self._unit_of_work() as uow:
            context = self._load(uow, user_id, update_id)
        status = context.diagnosis.summary.status
        if status in (DiagnosisStatus.COMPLETED, DiagnosisStatus.AWAITING_CLARIFICATION):
            return context.diagnosis
        required = missing_required(context.photos)
        if required:
            raise PhotosMissing([kind.value for kind in required])
        summary = context.diagnosis.summary
        if status is DiagnosisStatus.PENDING and summary.clarifying_answer is not None:
            attempt = self._resume_reevaluation(context.diagnosis)
        else:
            attempt = self._claim(user_id, context.diagnosis)
        return self._execute(user_id, context, attempt)

    @staticmethod
    def _resume_reevaluation(diagnosis: Diagnosis) -> Attempt:
        """The reservation of a re-evaluation is already committed: there is nothing to reserve.

        A repeated request may run the model twice, but only the first result is stored.
        """
        effort = ModelEffort(diagnosis.summary.model_effort)
        return Attempt(diagnosis.ai_reservation_id, effort, paid=False)

    def answer_clarification(
        self, user_id: uuid.UUID, update_id: uuid.UUID, text: str
    ) -> Diagnosis:
        """Re-evaluate with the user's answer; it reuses the committed reservation and is free."""
        answer = validate_clarification_answer(text)
        with self._unit_of_work() as uow:
            diagnosis = self._load(uow, user_id, update_id).diagnosis
            if diagnosis.summary.status is not DiagnosisStatus.AWAITING_CLARIFICATION:
                raise DiagnosisConflict("the diagnosis is not waiting for a clarification")
            if not uow.diagnoses.answer_clarification(update_id, answer):
                raise DiagnosisConflict("the clarification was already answered")
            uow.commit()
        with self._unit_of_work() as uow:
            context = self._load(uow, user_id, update_id)
        return self._execute(user_id, context, self._resume_reevaluation(context.diagnosis))

    def decline_escalation(self, user_id: uuid.UUID, update_id: uuid.UUID) -> Diagnosis:
        with self._unit_of_work() as uow:
            diagnosis = self._load(uow, user_id, update_id).diagnosis
            if uow.diagnoses.decline_escalation(update_id):
                uow.commit()
            elif not diagnosis.summary.escalation_declined:
                raise DiagnosisConflict("no expert escalation is on offer")
            return self._load(uow, user_id, update_id).diagnosis

    @staticmethod
    def _load(uow: UnitOfWork, user_id: uuid.UUID, update_id: uuid.UUID) -> Context:
        update = uow.updates.get_owned(update_id, user_id)
        if update is None:
            raise UpdateNotFound
        journal = uow.journals.get_owned(update.journal_id, user_id)
        diagnosis = uow.diagnoses.get(update_id)
        if journal is None or diagnosis is None:
            raise DiagnosisNotFound
        return Context(update, journal.species, uow.photos.list_for_update(update_id), diagnosis)

    def _claim(self, user_id: uuid.UUID, diagnosis: Diagnosis) -> Attempt:
        """Take the diagnosis for this attempt, with the reservation it will be paid from.

        Whether the previous attempt is still alive is decided by replaying its reservation id in
        entitlement: live means running (409), released or expired means a new id is needed.
        Nothing is written before entitlement has answered.
        """
        current = diagnosis.ai_reservation_id
        summary = diagnosis.summary
        if summary.status is DiagnosisStatus.PENDING and self._reservation_usable(current, user_id):
            if summary.model_effort is not None:
                raise DiagnosisConflict("another attempt is already running")
            effort = self._effort_policy.effort_for(user_id)
            claimed = self._claimed(
                lambda uow: uow.diagnoses.claim_first_attempt(diagnosis.update_id, current, effort)
            )
            if not claimed:
                raise DiagnosisConflict("another attempt is already running")
            return Attempt(current, effort, paid=True)
        return self._claim_with_new_reservation(user_id, diagnosis)

    def _reservation_usable(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Reserve is idempotent by id, so it also makes a reservation that was never made."""
        try:
            self._ai_quota.reserve(reservation_id, user_id)
        except ReservationUnusable:
            return False
        return True

    def _claim_with_new_reservation(self, user_id: uuid.UUID, diagnosis: Diagnosis) -> Attempt:
        new_id = uuid.uuid4()
        self._ai_quota.reserve(new_id, user_id)
        effort = self._effort_policy.effort_for(user_id)
        claimed = self._claimed(
            lambda uow: uow.diagnoses.claim_retry(
                diagnosis.update_id, diagnosis.ai_reservation_id, new_id, effort
            )
        )
        if not claimed:
            self._release(new_id, user_id)
            raise DiagnosisConflict("another attempt is already running")
        return Attempt(new_id, effort, paid=True)

    def _claimed(self, claim: Callable[[UnitOfWork], bool]) -> bool:
        with self._unit_of_work() as uow:
            claimed = claim(uow)
            if claimed:
                uow.commit()
        return claimed

    def _execute(self, user_id: uuid.UUID, context: Context, attempt: Attempt) -> Diagnosis:
        summary = context.diagnosis.summary
        request = DiagnosisRequest(
            species=context.species,
            update=context.update,
            photos=context.photos,
            effort=attempt.effort,
            clarifying_question=summary.clarifying_question,
            clarifying_answer=summary.clarifying_answer,
            temperature=self._sample_temperature if self._samples > 1 else None,
        )
        update_id = context.diagnosis.update_id
        try:
            result = decide(
                collect_answers(self._model, request, self._samples),
                samples=self._samples,
                rule=self._dominance_rule,
                clarified=summary.clarifying_answer is not None,
            )
        except ModelFailure as failure:
            logger.warning(
                "the model gave no usable answer, the diagnosis degrades",
                extra={"update_id": str(update_id), "failure": type(failure).__name__},
            )
            return self._degrade(user_id, update_id, attempt)
        return self._store(user_id, update_id, attempt, result)

    def _store(
        self, user_id: uuid.UUID, update_id: uuid.UUID, attempt: Attempt, result: DiagnosisResult
    ) -> Diagnosis:
        with self._unit_of_work() as uow:
            stored = uow.diagnoses.record_result(update_id, attempt.reservation_id, result)
            if stored:
                uow.commit()
        if not stored:
            self._abandon(user_id, attempt)
            raise DiagnosisConflict("another attempt took over the diagnosis")
        if attempt.paid:
            self._commit(attempt.reservation_id, user_id)
        logger.info(
            "diagnosis recorded",
            extra={"update_id": str(update_id), "diagnosis_status": result.status.value},
        )
        return self._reload(update_id)

    def _degrade(self, user_id: uuid.UUID, update_id: uuid.UUID, attempt: Attempt) -> Diagnosis:
        with self._unit_of_work() as uow:
            marked = uow.diagnoses.mark_degraded(
                update_id, attempt.reservation_id, FALLBACK_RECOMMENDATION
            )
            if marked:
                uow.commit()
        self._abandon(user_id, attempt)
        if not marked:
            raise DiagnosisConflict("another attempt took over the diagnosis")
        return self._reload(update_id)

    def _reload(self, update_id: uuid.UUID) -> Diagnosis:
        with self._unit_of_work() as uow:
            diagnosis = uow.diagnoses.get(update_id)
        if diagnosis is None:
            raise DiagnosisNotFound
        return diagnosis

    def _abandon(self, user_id: uuid.UUID, attempt: Attempt) -> None:
        if attempt.paid:
            self._release(attempt.reservation_id, user_id)

    def _commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        try:
            self._ai_quota.commit(reservation_id, user_id)
        except (AiQuotaUnavailable, ReservationUnusable):
            logger.warning(
                "ai quota reservation could not be committed, the diagnosis stays unpaid",
                extra={"reservation_id": str(reservation_id)},
            )

    def _release(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        try:
            self._ai_quota.release(reservation_id, user_id)
        except (AiQuotaUnavailable, ReservationUnusable):
            logger.warning(
                "ai quota reservation could not be released, it will expire",
                extra={"reservation_id": str(reservation_id)},
            )
