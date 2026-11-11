import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PlacedOrder
from tests.db.factories import (
    RESERVATION_MODELS,
    add_placed_order,
    add_reservation,
    key_name,
    placed_order_values,
    reservation_values,
    violated,
)

by_model = pytest.mark.parametrize("model", RESERVATION_MODELS, ids=lambda m: m.__tablename__)


def test_placed_order_accepts_a_free_order(session: Session):
    add_placed_order(session, total_paid_cents=0)

    assert session.scalar(select(PlacedOrder.total_paid_cents)) == 0


def test_placed_order_rejects_a_negative_amount(session: Session):
    with violated(session) as violation:
        add_placed_order(session, total_paid_cents=-1)

    assert violation.constraint == "ck_placed_orders_total_paid_non_negative"


def test_placed_order_id_is_unique(session: Session):
    order = add_placed_order(session)

    with violated(session) as violation:
        session.execute(
            insert(PlacedOrder).values(**placed_order_values(order_id=order["order_id"]))
        )

    assert violation.constraint == "pk_placed_orders"


@by_model
def test_reservation_starts_reserved_and_is_stamped_by_the_database(session: Session, model):
    values = add_reservation(session, model)

    row = session.execute(select(model).where(model.user_id == values["user_id"])).scalar_one()

    assert row.status == "reserved"
    assert abs(datetime.now(UTC) - row.created_at) < timedelta(minutes=1)


@by_model
@pytest.mark.parametrize("status", ["reserved", "committed", "released"])
def test_reservation_accepts_every_documented_status(session: Session, model, status: str):
    add_reservation(session, model, status=status)


@by_model
@pytest.mark.parametrize("status", ["expired", "RESERVED", ""])
def test_reservation_rejects_other_statuses(session: Session, model, status: str):
    with violated(session) as violation:
        add_reservation(session, model, status=status)

    assert violation.constraint == f"ck_{model.__tablename__}_status"


@by_model
def test_reservation_cannot_be_updated_to_an_unknown_status(session: Session, model):
    values = add_reservation(session, model)
    key = getattr(model, key_name(model))

    with violated(session) as violation:
        session.execute(update(model).where(key == values[key_name(model)]).values(status="gone"))

    assert violation.constraint == f"ck_{model.__tablename__}_status"


@by_model
@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=-1)], ids=["equal", "before"])
def test_reservation_must_expire_after_it_was_created(session: Session, model, offset: timedelta):
    created_at = datetime.now(UTC)

    with violated(session) as violation:
        add_reservation(session, model, created_at=created_at, expires_at=created_at + offset)

    assert violation.constraint == f"ck_{model.__tablename__}_expiry"


@by_model
def test_reservation_id_is_unique_even_across_users(session: Session, model):
    first = add_reservation(session, model)

    with violated(session) as violation:
        add_reservation(session, model, **{key_name(model): first[key_name(model)]})

    assert violation.constraint == f"pk_{model.__tablename__}"


@by_model
def test_reservation_requires_a_user(session: Session, model):
    values = reservation_values(model)
    del values["user_id"]

    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(insert(model).values(**values))

    assert error.value.orig.diag.column_name == "user_id"


def test_ai_and_expert_tables_are_independent(session: Session):
    shared_id = uuid.uuid4()

    for model in RESERVATION_MODELS:
        add_reservation(session, model, **{key_name(model): shared_id})
