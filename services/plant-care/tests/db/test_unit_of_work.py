import uuid
from collections.abc import Callable

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.models import JournalModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.model import NewJournal


def count_journals(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(JournalModel))


def new_journal() -> NewJournal:
    return NewJournal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Basil")


def test_committed_work_is_visible_to_the_next_unit(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    with unit_of_work() as uow:
        uow.journals.add(new_journal())
        uow.commit()

    assert count_journals(engine) == 1


def test_leaving_the_block_without_a_commit_rolls_back(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    with unit_of_work() as uow:
        uow.journals.add(new_journal())

    assert count_journals(engine) == 0


def test_an_exception_rolls_the_whole_unit_back(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    try:
        with unit_of_work() as uow:
            uow.journals.add(new_journal())
            raise RuntimeError("failure before the commit")
    except RuntimeError:
        pass

    assert count_journals(engine) == 0
