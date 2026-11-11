import uuid
from datetime import timedelta
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Text,
    and_,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AiQuotaReservation, ExpertQuotaReservation
from app.service.errors import LockTimeout
from app.service.model import Reservation, ReservationStatus

LOCK_NOT_AVAILABLE = "55P03"
EXPERT_WINDOW = timedelta(days=30)


class _SqlAlchemyReservations:
    """Shared queries; a subclass names its table, lock namespace and counting window."""

    model: ClassVar[type[AiQuotaReservation] | type[ExpertQuotaReservation]]
    lock_prefix: ClassVar[str]

    def __init__(self, session: Session, lock_timeout: timedelta) -> None:
        self._session = session
        self._lock_timeout = lock_timeout

    @property
    def _key(self):
        (column,) = self.model.__table__.primary_key.columns
        return getattr(self.model, column.key)

    def _in_window(self) -> ColumnElement[bool]:
        raise NotImplementedError

    def _columns(self) -> tuple:
        model = self.model
        return (
            self._key.label("id"),
            model.user_id,
            model.status,
            model.expires_at,
            (model.expires_at > func.now()).label("unexpired"),
        )

    @staticmethod
    def _to_reservation(row) -> Reservation:
        return Reservation(**{**row._mapping, "status": ReservationStatus(row.status)})

    def lock_user(self, user_id: uuid.UUID) -> None:
        milliseconds = int(self._lock_timeout.total_seconds() * 1000)
        self._session.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{milliseconds}ms"},
        )
        lock_key = func.hashtextextended(
            literal(self.lock_prefix, Text).concat(cast(user_id, Text)), literal(0, BigInteger)
        )
        try:
            self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
                raise LockTimeout from error
            raise

    def get(self, reservation_id: uuid.UUID) -> Reservation | None:
        row = self._session.execute(
            select(*self._columns()).where(self._key == reservation_id)
        ).one_or_none()
        return None if row is None else self._to_reservation(row)

    def get_owned(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> Reservation | None:
        row = self._session.execute(
            select(*self._columns()).where(
                self._key == reservation_id, self.model.user_id == user_id
            )
        ).one_or_none()
        return None if row is None else self._to_reservation(row)

    def count_used(self, user_id: uuid.UUID) -> int:
        model = self.model
        return self._session.scalar(
            select(func.count()).where(
                model.user_id == user_id,
                self._in_window(),
                or_(
                    model.status == ReservationStatus.COMMITTED.value,
                    and_(
                        model.status == ReservationStatus.RESERVED.value,
                        model.expires_at > func.now(),
                    ),
                ),
            )
        )

    def insert(
        self, reservation_id: uuid.UUID, user_id: uuid.UUID, ttl: timedelta
    ) -> Reservation | None:
        expires_at = text("now() + make_interval(secs => :ttl)").bindparams(ttl=ttl.total_seconds())
        row = self._session.execute(
            insert(self.model)
            .values({self._key.key: reservation_id, "user_id": user_id, "expires_at": expires_at})
            .on_conflict_do_nothing()
            .returning(*self._columns())
        ).one_or_none()
        return None if row is None else self._to_reservation(row)

    def transition(
        self,
        reservation_id: uuid.UUID,
        user_id: uuid.UUID,
        target: ReservationStatus,
        sources: frozenset[ReservationStatus],
        unexpired_only: bool,
    ) -> Reservation | None:
        model = self.model
        statement = update(model).where(
            self._key == reservation_id,
            model.user_id == user_id,
            model.status.in_(sorted(source.value for source in sources)),
        )
        if unexpired_only:
            statement = statement.where(model.expires_at > func.now())
        row = self._session.execute(
            statement.values(status=target.value)
            .returning(*self._columns())
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return None if row is None else self._to_reservation(row)


class SqlAlchemyAiReservations(_SqlAlchemyReservations):
    model = AiQuotaReservation
    lock_prefix = "ai:"

    def _in_window(self) -> ColumnElement[bool]:
        today_utc = func.date_trunc(literal_column("'day'"), func.now(), literal_column("'UTC'"))
        return AiQuotaReservation.created_at >= today_utc


class SqlAlchemyExpertReservations(_SqlAlchemyReservations):
    model = ExpertQuotaReservation
    lock_prefix = "expert:"

    def _in_window(self) -> ColumnElement[bool]:
        return ExpertQuotaReservation.created_at > func.now() - EXPERT_WINDOW
