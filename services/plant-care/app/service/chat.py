import logging
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.service.ai_quota import AiQuota
from app.service.chat_answer import ResolvedAnswer, resolve_answer
from app.service.chat_model import (
    ChatModel,
    ChatPage,
    ChatReply,
    ChatRequest,
    ChatTurn,
    NewChatTurn,
    PriorTurn,
)
from app.service.cursor import decode_cursor, encode_cursor
from app.service.diagnosis_model import ModelFailure
from app.service.effort import EffortPolicy
from app.service.errors import (
    AiQuotaUnavailable,
    ChatTurnNotFound,
    InvalidRequest,
    JournalNotFound,
    ReservationUnusable,
)
from app.service.knowledge_model import RetrievedSection
from app.service.model import FeedCursor, ModelEffort
from app.service.ports import UnitOfWork

logger = logging.getLogger(__name__)

QUESTION_LIMIT = 1000


@dataclass(frozen=True)
class Preparation:
    """What a new turn starts from: the plant, the retrieved sections and the earlier turns."""

    species: str
    sections: list[RetrievedSection]
    history: list[PriorTurn]


def validate_question(text: str) -> str:
    question = unicodedata.normalize("NFC", text).strip()
    if not 1 <= len(question) <= QUESTION_LIMIT:
        raise InvalidRequest(f"the question must have 1 to {QUESTION_LIMIT} characters")
    if any(unicodedata.category(char) == "Cc" and char != "\n" for char in question):
        raise InvalidRequest("the question contains control characters")
    return question


class ChatService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        ai_quota: AiQuota,
        model: ChatModel,
        effort_policy: EffortPolicy,
        top_k: int,
        history_turns: int,
        default_page_size: int,
        max_page_size: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._ai_quota = ai_quota
        self._model = model
        self._effort_policy = effort_policy
        self._top_k = top_k
        self._history_turns = history_turns
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

    def ask(
        self, user_id: uuid.UUID, journal_id: uuid.UUID, turn_id: uuid.UUID, question: str
    ) -> ChatReply:
        """Answer the question from the knowledge base, or return the stored turn of a retried id.

        When the quota service or the model cannot be used the reply has no turn: nothing is
        stored, no quota is spent, and the client may retry with the same id.
        """
        text = validate_question(question)
        prepared = self._prepare(user_id, journal_id, turn_id, text)
        if isinstance(prepared, ChatTurn):
            return ChatReply(prepared, created=False, related=[])
        effort = self._effort_policy.effort_for(user_id)
        reservation_id = uuid.uuid4()
        try:
            self._ai_quota.reserve(reservation_id, user_id)
        except (AiQuotaUnavailable, ReservationUnusable):
            logger.warning(
                "ai quota unavailable, the chat answers with sources only",
                extra={"turn_id": str(turn_id), "journal_id": str(journal_id)},
            )
            return ChatReply(None, created=False, related=prepared.sections)
        return self._answer(user_id, journal_id, turn_id, text, prepared, effort, reservation_id)

    def history(
        self, user_id: uuid.UUID, journal_id: uuid.UUID, limit: int | None, cursor: str | None
    ) -> ChatPage:
        page_size = min(limit or self._default_page_size, self._max_page_size)
        before = decode_cursor(cursor) if cursor is not None else None
        with self._unit_of_work() as uow:
            if uow.journals.get_owned(journal_id, user_id) is None:
                raise JournalNotFound
            turns = uow.chat_turns.page(journal_id, page_size + 1, before)
        if len(turns) <= page_size:
            return ChatPage(turns, None)
        items = turns[:page_size]
        return ChatPage(items, encode_cursor(FeedCursor(items[-1].created_at, items[-1].id)))

    def _prepare(
        self, user_id: uuid.UUID, journal_id: uuid.UUID, turn_id: uuid.UUID, question: str
    ) -> ChatTurn | Preparation:
        with self._unit_of_work() as uow:
            journal = uow.journals.get_owned(journal_id, user_id)
            if journal is None:
                raise JournalNotFound
            stored = uow.chat_turns.get(turn_id)
            if stored is not None:
                return self._same_journal(stored, journal_id)
            sections = uow.knowledge.search(question, journal.species, self._top_k)
            recent = uow.chat_turns.page(journal_id, self._history_turns, None)
        history = [PriorTurn(turn.question, turn.answer) for turn in reversed(recent)]
        return Preparation(journal.species, sections, history)

    @staticmethod
    def _same_journal(turn: ChatTurn, journal_id: uuid.UUID) -> ChatTurn:
        """An id that already belongs to another journal must look like a missing turn."""
        if turn.journal_id != journal_id:
            raise ChatTurnNotFound
        return turn

    def _answer(
        self,
        user_id: uuid.UUID,
        journal_id: uuid.UUID,
        turn_id: uuid.UUID,
        question: str,
        prepared: Preparation,
        effort: ModelEffort,
        reservation_id: uuid.UUID,
    ) -> ChatReply:
        try:
            resolved, model_ms = self._generate(question, prepared, effort)
            turn = NewChatTurn(
                turn_id,
                journal_id,
                question,
                resolved.outcome,
                resolved.answer,
                reservation_id,
                effort,
                resolved.sources,
            )
            stored, created = self._store(turn)
        except ModelFailure as failure:
            logger.warning(
                "the model gave no usable answer, the chat answers with sources only",
                extra={"turn_id": str(turn_id), "failure": type(failure).__name__},
            )
            self._release(reservation_id, user_id)
            return ChatReply(None, created=False, related=prepared.sections)
        except Exception:
            self._release(reservation_id, user_id)
            raise
        if not created:
            self._release(reservation_id, user_id)
            return ChatReply(stored, created=False, related=[])
        self._commit(reservation_id, user_id)
        self._log_turn(stored, prepared, resolved, reservation_id, model_ms, len(question))
        return ChatReply(stored, created=True, related=[])

    def _generate(
        self, question: str, prepared: Preparation, effort: ModelEffort
    ) -> tuple[ResolvedAnswer, int]:
        """The checked answer and how long the model took, in milliseconds."""
        request = ChatRequest(
            question, prepared.species, prepared.sections, prepared.history, effort
        )
        started = time.monotonic()
        answer = self._model.answer(request)
        model_ms = round((time.monotonic() - started) * 1000)
        return resolve_answer(answer, prepared.sections), model_ms

    def _store(self, turn: NewChatTurn) -> tuple[ChatTurn, bool]:
        with self._unit_of_work() as uow:
            created = uow.chat_turns.add(turn)
            if created:
                uow.commit()
            stored = uow.chat_turns.get(turn.id)
        if stored is None:
            raise ChatTurnNotFound
        return self._same_journal(stored, turn.journal_id), created

    @staticmethod
    def _log_turn(
        turn: ChatTurn,
        prepared: Preparation,
        resolved: ResolvedAnswer,
        reservation_id: uuid.UUID,
        model_ms: int,
        question_length: int,
    ) -> None:
        logger.info(
            "chat turn stored",
            extra={
                "turn_id": str(turn.id),
                "journal_id": str(turn.journal_id),
                "question_length": question_length,
                "chat_outcome": resolved.outcome.value,
                "retrieved_section_ids": [str(section.id) for section in prepared.sections],
                "cited_section_ids": [str(source.section_id) for source in resolved.sources],
                "model_ms": model_ms,
                "reservation_id": str(reservation_id),
            },
        )

    def _commit(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        try:
            self._ai_quota.commit(reservation_id, user_id)
        except (AiQuotaUnavailable, ReservationUnusable):
            logger.warning(
                "ai quota reservation could not be committed, the chat turn stays unpaid",
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
