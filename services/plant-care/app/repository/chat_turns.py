import uuid
from collections import defaultdict

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models import ChatTurnModel, ChatTurnSourceModel
from app.service.chat_model import ChatOutcome, ChatTurn, Citation, NewChatTurn
from app.service.model import FeedCursor, ModelEffort

TURN_COLUMNS = (
    ChatTurnModel.id,
    ChatTurnModel.journal_id,
    ChatTurnModel.question,
    ChatTurnModel.outcome,
    ChatTurnModel.answer,
    ChatTurnModel.model_effort,
    ChatTurnModel.created_at,
)


class SqlAlchemyChatTurnRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, turn: NewChatTurn) -> bool:
        """The citations are inserted only with a new turn: a stored one already has its own."""
        inserted = self._session.execute(
            insert(ChatTurnModel)
            .values(
                id=turn.id,
                journal_id=turn.journal_id,
                question=turn.question,
                outcome=turn.outcome.value,
                answer=turn.answer,
                ai_reservation_id=turn.ai_reservation_id,
                model_effort=turn.model_effort.value,
            )
            .on_conflict_do_nothing(index_elements=[ChatTurnModel.id])
            .returning(ChatTurnModel.id)
        ).scalar_one_or_none()
        if inserted is None:
            return False
        if turn.sources:
            self._session.execute(
                insert(ChatTurnSourceModel),
                [
                    {
                        "turn_id": turn.id,
                        "rank": source.rank,
                        "section_id": source.section_id,
                        "document_title": source.document_title,
                        "section_heading": source.section_heading,
                        "source_url": source.source_url,
                    }
                    for source in turn.sources
                ],
            )
        return True

    def get(self, turn_id: uuid.UUID) -> ChatTurn | None:
        rows = self._session.execute(select(*TURN_COLUMNS).where(ChatTurnModel.id == turn_id)).all()
        turns = self._with_sources(rows)
        return turns[0] if turns else None

    def page(self, journal_id: uuid.UUID, limit: int, before: FeedCursor | None) -> list[ChatTurn]:
        statement = (
            select(*TURN_COLUMNS)
            .where(ChatTurnModel.journal_id == journal_id)
            .order_by(ChatTurnModel.created_at.desc(), ChatTurnModel.id.desc())
            .limit(limit)
        )
        if before is not None:
            statement = statement.where(
                tuple_(ChatTurnModel.created_at, ChatTurnModel.id)
                < tuple_(before.created_at, before.id)
            )
        return self._with_sources(self._session.execute(statement).all())

    def _with_sources(self, rows: list[Row]) -> list[ChatTurn]:
        if not rows:
            return []
        sources: dict[uuid.UUID, list[Citation]] = defaultdict(list)
        cited = self._session.execute(
            select(ChatTurnSourceModel)
            .where(ChatTurnSourceModel.turn_id.in_([row.id for row in rows]))
            .order_by(ChatTurnSourceModel.turn_id, ChatTurnSourceModel.rank)
        ).scalars()
        for source in cited:
            sources[source.turn_id].append(
                Citation(
                    source.rank,
                    source.section_id,
                    source.document_title,
                    source.section_heading,
                    source.source_url,
                )
            )
        return [
            ChatTurn(
                id=row.id,
                journal_id=row.journal_id,
                question=row.question,
                outcome=ChatOutcome(row.outcome),
                answer=row.answer,
                model_effort=ModelEffort(row.model_effort),
                created_at=row.created_at,
                sources=sources[row.id],
            )
            for row in rows
        ]
