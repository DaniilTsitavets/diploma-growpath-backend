import logging
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.service.errors import (
    EscalationNotFound,
    EscalationNotOffered,
    ExpertQuotaExhausted,
    InvalidRequest,
    UpdateNotFound,
)
from app.service.expert_quota import ExpertQuota
from app.service.model import Escalation, EscalationStatus, NewEscalation
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)

QUESTION_LIMIT = 1000


class EscalationAnnouncer(Protocol):
    """Tells the expert service about a `requested` escalation; never raises."""

    def announce(self, escalation: Escalation) -> None: ...


@dataclass(frozen=True)
class Requested:
    escalation: Escalation
    created: bool


def validate_question(text: str) -> str:
    question = unicodedata.normalize("NFC", text).strip()
    if not 1 <= len(question) <= QUESTION_LIMIT:
        raise InvalidRequest(f"the question must have 1 to {QUESTION_LIMIT} characters")
    if any(unicodedata.category(char) == "Cc" and char != "\n" for char in question):
        raise InvalidRequest("the question contains control characters")
    return question


class EscalationService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        expert_quota: ExpertQuota,
        announcer: EscalationAnnouncer | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._expert_quota = expert_quota
        self._announcer = announcer

    def get(self, user_id: uuid.UUID, update_id: uuid.UUID) -> Escalation:
        with self._unit_of_work() as uow:
            self._require_update(uow, user_id, update_id)
            escalation = uow.escalations.latest(update_id)
        if escalation is None:
            raise EscalationNotFound
        return escalation

    def request(
        self, user_id: uuid.UUID, update_id: uuid.UUID, user_question: str | None
    ) -> Requested:
        """Ask an expert: the offer after a diagnosis, or a manual question of the user.

        Repeating the request returns the active escalation, and republishes the event while no
        ticket has confirmed it.
        """
        question = None if user_question is None else validate_question(user_question)
        with self._unit_of_work() as uow:
            self._require_update(uow, user_id, update_id)
            active = uow.escalations.get_active(update_id)
            if active is None and question is None:
                self._require_offer(uow, update_id)
        if active is None:
            active = self._create(user_id, update_id, question)
            if active is not None:
                self._announce(active)
                return Requested(active, created=True)
            active = self._active_after_race(update_id)
        if active.status is EscalationStatus.REQUESTED:
            self._announce(active)
        return Requested(active, created=False)

    @staticmethod
    def _require_update(uow: UnitOfWork, user_id: uuid.UUID, update_id: uuid.UUID) -> None:
        if uow.updates.get_owned(update_id, user_id) is None:
            raise UpdateNotFound

    @staticmethod
    def _require_offer(uow: UnitOfWork, update_id: uuid.UUID) -> None:
        diagnosis = uow.diagnoses.get(update_id)
        if diagnosis is None:
            raise EscalationNotOffered
        summary = diagnosis.summary
        if not summary.escalation_offered or summary.escalation_declined:
            raise EscalationNotOffered

    def _create(
        self, user_id: uuid.UUID, update_id: uuid.UUID, question: str | None
    ) -> Escalation | None:
        """Insert the escalation and reserve the expert quota in one transaction.

        The reservation is made while the transaction is open, so a refusal rolls the row back. It
        may exist in entitlement even when the call failed or the commit did not happen, hence the
        release on every failure except a refusal. None means another request won the update.
        """
        escalation_id = uuid.uuid4()
        try:
            with self._unit_of_work() as uow:
                if not uow.escalations.add(NewEscalation(escalation_id, update_id, question)):
                    return None
                self._expert_quota.reserve_expert(escalation_id, user_id)
                uow.commit()
        except ExpertQuotaExhausted:
            raise
        except Exception:
            self._release_quietly(escalation_id, user_id)
            raise
        with self._unit_of_work() as uow:
            escalation = uow.escalations.get(escalation_id)
        if escalation is None:
            raise RuntimeError("the committed escalation cannot be read")
        logger.info(
            "escalation requested",
            extra={"escalation_id": str(escalation_id), "update_id": str(update_id)},
        )
        return escalation

    def _active_after_race(self, update_id: uuid.UUID) -> Escalation:
        with self._unit_of_work() as uow:
            active = uow.escalations.get_active(update_id)
        if active is None:
            raise RuntimeError("an active escalation conflicted but cannot be read")
        return active

    def _release_quietly(self, escalation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        try:
            self._expert_quota.release_expert(escalation_id, user_id)
        except Exception:
            logger.warning(
                "expert quota reservation could not be released, it will expire",
                extra={"escalation_id": str(escalation_id)},
                exc_info=True,
            )

    def _announce(self, escalation: Escalation) -> None:
        if self._announcer is not None:
            self._announcer.announce(escalation)
