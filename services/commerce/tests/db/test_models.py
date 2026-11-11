import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, Pack, PackLabel


def make_pack(**overrides) -> Pack:
    fields = {"name": "Basil Kit", "species": "basil", "seed_count": 10, "price_cents": 2500}
    return Pack(**{**fields, **overrides})


def make_order(pack: Pack, **overrides) -> Order:
    fields = {
        "user_id": "user-1",
        "pack_id": pack.id,
        "list_price_cents": 2500,
        "discount_cents": 500,
        "total_paid_cents": 2000,
    }
    return Order(**{**fields, **overrides})


def test_pack_is_active_by_default_and_loads_its_labels(session: Session):
    pack = make_pack(labels=[PackLabel(label="rare"), PackLabel(label="low-maintenance")])
    session.add(pack)
    session.commit()
    session.expire_all()

    stored = session.scalars(select(Pack)).one()
    assert stored.active is True
    assert {label.label for label in stored.labels} == {"rare", "low-maintenance"}


def test_sensor_pack_has_no_species(session: Session):
    session.add(make_pack(name="Soil sensor", species=None, seed_count=0))
    session.commit()

    assert session.scalars(select(Pack)).one().species is None


def test_deleting_pack_removes_its_labels(session: Session):
    pack = make_pack(labels=[PackLabel(label="rare")])
    session.add(pack)
    session.commit()

    session.delete(pack)
    session.commit()

    assert session.scalars(select(PackLabel)).all() == []


@pytest.mark.parametrize(
    "overrides",
    [{"price_cents": -1}, {"seed_count": -1}],
    ids=["negative price", "negative seed count"],
)
def test_pack_rejects_negative_amounts(session: Session, overrides: dict):
    session.add(make_pack(**overrides))

    with pytest.raises(IntegrityError):
        session.commit()


def test_pack_names_are_unique(session: Session):
    session.add_all([make_pack(), make_pack()])

    with pytest.raises(IntegrityError):
        session.commit()


def test_pack_cannot_repeat_a_label(session: Session):
    session.add(make_pack(labels=[PackLabel(label="rare"), PackLabel(label="rare")]))

    with pytest.raises(IntegrityError):
        session.commit()


def test_order_stores_amounts_and_timestamp(session: Session):
    pack = make_pack()
    session.add(pack)
    session.flush()
    session.add(make_order(pack))
    session.commit()

    order = session.scalars(select(Order)).one()
    assert (order.list_price_cents, order.discount_cents, order.total_paid_cents) == (
        2500,
        500,
        2000,
    )
    assert order.created_at is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_paid_cents": 2500},
        {"discount_cents": 3000, "total_paid_cents": -500},
        {"discount_cents": -100, "total_paid_cents": 2600},
        {"list_price_cents": -1, "discount_cents": 0, "total_paid_cents": -1},
    ],
    ids=["total ignores discount", "discount above price", "negative discount", "negative price"],
)
def test_order_rejects_inconsistent_amounts(session: Session, overrides: dict):
    pack = make_pack()
    session.add(pack)
    session.flush()
    session.add(make_order(pack, **overrides))

    with pytest.raises(IntegrityError):
        session.commit()


def test_order_requires_an_existing_pack(session: Session):
    session.add(make_order(make_pack(id=uuid.uuid4())))

    with pytest.raises(IntegrityError):
        session.commit()
