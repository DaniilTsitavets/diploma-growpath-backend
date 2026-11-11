import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.models import AiQuotaReservation, ExpertQuotaReservation
from app.repository.reservations import SqlAlchemyAiReservations, SqlAlchemyExpertReservations
from app.service.model import ReservationStatus
from tests.db.factories import add_reservation, db_now, key_name

RESERVED = ReservationStatus.RESERVED
COMMITTED = ReservationStatus.COMMITTED
RELEASED = ReservationStatus.RELEASED

both = pytest.mark.parametrize(
    ("repository_class", "model"),
    [
        (SqlAlchemyAiReservations, AiQuotaReservation),
        (SqlAlchemyExpertReservations, ExpertQuotaReservation),
    ],
    ids=["ai", "expert"],
)


def make(repository_class, session: Session):
    return repository_class(session, timedelta(seconds=2))


def reservation_id(values: dict, model) -> uuid.UUID:
    return values[key_name(model)]


@both
def test_insert_stores_a_reserved_row_that_lives_for_the_ttl(repository_class, model, session):
    repository = make(repository_class, session)
    new_id, user_id = uuid.uuid4(), uuid.uuid4()

    created = repository.insert(new_id, user_id, timedelta(minutes=5))

    row = session.execute(select(model)).scalar_one()
    assert (created.id, created.user_id, created.status) == (new_id, user_id, RESERVED)
    assert created.unexpired
    assert row.expires_at - row.created_at == timedelta(minutes=5)


@both
def test_insert_with_a_taken_id_returns_nothing_and_changes_nothing(
    repository_class, model, session
):
    repository = make(repository_class, session)
    taken = add_reservation(session, model, status="committed")

    created = repository.insert(reservation_id(taken, model), uuid.uuid4(), timedelta(minutes=5))

    row = session.execute(select(model)).scalar_one()
    assert created is None
    assert (row.user_id, row.status) == (taken["user_id"], "committed")


@both
def test_get_returns_the_row_and_whether_it_is_still_unexpired(repository_class, model, session):
    repository = make(repository_class, session)
    now = db_now(session)
    live = add_reservation(session, model)
    dead = add_reservation(
        session,
        model,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )

    found_live = repository.get(reservation_id(live, model))
    found_dead = repository.get(reservation_id(dead, model))

    assert (found_live.user_id, found_live.status, found_live.unexpired) == (
        live["user_id"],
        RESERVED,
        True,
    )
    assert found_dead.unexpired is False
    assert repository.get(uuid.uuid4()) is None


@both
def test_get_owned_hides_rows_of_other_users(repository_class, model, session):
    repository = make(repository_class, session)
    values = add_reservation(session, model)

    assert repository.get_owned(reservation_id(values, model), values["user_id"]) is not None
    assert repository.get_owned(reservation_id(values, model), uuid.uuid4()) is None


def test_ai_quota_counts_the_utc_day_of_committed_and_unexpired_reserved_rows(session: Session):
    repository = make(SqlAlchemyAiReservations, session)
    user_id = uuid.uuid4()
    now = db_now(session)
    midnight = session.scalar(
        select(func.date_trunc(literal_column("'day'"), func.now(), literal_column("'UTC'")))
    )
    in_an_hour = now + timedelta(hours=1)
    counted = [
        {"status": "committed"},
        {"status": "reserved"},
        {"status": "committed", "created_at": midnight},
    ]
    ignored = [
        {"status": "released"},
        {"status": "reserved", "created_at": now - timedelta(hours=2), "expires_at": now},
        {"status": "committed", "created_at": midnight - timedelta(seconds=1)},
    ]
    for overrides in counted + ignored:
        add_reservation(
            session, AiQuotaReservation, user_id=user_id, **{"expires_at": in_an_hour, **overrides}
        )
    add_reservation(session, AiQuotaReservation, status="committed")

    assert repository.count_used(user_id) == len(counted)


def test_expert_quota_counts_the_last_30_days(session: Session):
    repository = make(SqlAlchemyExpertReservations, session)
    user_id = uuid.uuid4()
    now = db_now(session)
    in_an_hour = now + timedelta(hours=1)
    counted = [
        {"status": "committed", "created_at": now - timedelta(days=29)},
        {"status": "reserved"},
    ]
    ignored = [
        {"status": "committed", "created_at": now - timedelta(days=30)},
        {"status": "committed", "created_at": now - timedelta(days=31)},
        {"status": "released"},
        {"status": "reserved", "created_at": now - timedelta(hours=2), "expires_at": now},
    ]
    for overrides in counted + ignored:
        add_reservation(
            session,
            ExpertQuotaReservation,
            user_id=user_id,
            **{"expires_at": in_an_hour, **overrides},
        )
    add_reservation(session, ExpertQuotaReservation, status="committed")

    assert repository.count_used(user_id) == len(counted)


@both
@pytest.mark.parametrize(
    ("status", "target", "moved"),
    [
        ("reserved", COMMITTED, True),
        ("committed", COMMITTED, False),
        ("released", COMMITTED, False),
        ("reserved", RELEASED, True),
        ("committed", RELEASED, False),
    ],
)
def test_transition_moves_only_from_the_allowed_sources(
    repository_class, model, session, status, target, moved
):
    repository = make(repository_class, session)
    values = add_reservation(session, model, status=status)

    result = repository.transition(
        reservation_id(values, model),
        values["user_id"],
        target,
        sources=frozenset({RESERVED}),
        unexpired_only=False,
    )

    stored = session.execute(select(model.status)).scalar_one()
    assert (result is not None) is moved
    assert stored == (target.value if moved else status)


@both
def test_transition_can_start_from_several_sources(repository_class, model, session):
    repository = make(repository_class, session)
    values = add_reservation(session, model, status="committed")

    result = repository.transition(
        reservation_id(values, model),
        values["user_id"],
        RELEASED,
        sources=frozenset({RESERVED, COMMITTED}),
        unexpired_only=False,
    )

    assert result.status is RELEASED


@both
def test_transition_of_another_users_row_changes_nothing(repository_class, model, session):
    repository = make(repository_class, session)
    values = add_reservation(session, model)

    result = repository.transition(
        reservation_id(values, model),
        uuid.uuid4(),
        COMMITTED,
        sources=frozenset({RESERVED}),
        unexpired_only=False,
    )

    assert result is None
    assert session.execute(select(model.status)).scalar_one() == "reserved"


@both
@pytest.mark.parametrize(("unexpired_only", "moved"), [(True, False), (False, True)])
def test_expiry_guard_blocks_only_when_requested(
    repository_class, model, session, unexpired_only, moved
):
    repository = make(repository_class, session)
    now = db_now(session)
    values = add_reservation(
        session, model, created_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1)
    )

    result = repository.transition(
        reservation_id(values, model),
        values["user_id"],
        COMMITTED,
        sources=frozenset({RESERVED}),
        unexpired_only=unexpired_only,
    )

    assert (result is not None) is moved
