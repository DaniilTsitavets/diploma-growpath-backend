import uuid
from functools import partial

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import CatalogItem, Order
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.model import NewOrder


def draft(item: CatalogItem) -> NewOrder:
    return NewOrder(uuid.uuid4(), uuid.uuid4(), item.id, "key-1", 2500, 0, 2500)


def count_orders(engine: Engine, item: CatalogItem) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).where(Order.item_id == item.id))


def test_work_is_kept_only_after_commit(engine: Engine, committed_item: CatalogItem):
    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(engine))

    with unit_of_work() as uow:
        uow.orders.place(draft(committed_item))
    assert count_orders(engine, committed_item) == 0

    with unit_of_work() as uow:
        uow.orders.place(draft(committed_item))
        uow.commit()
    assert count_orders(engine, committed_item) == 1
