import uuid
from collections.abc import Callable
from functools import partial

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import EscalationModel
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.errors import ExpertQuotaExhausted, ExpertQuotaUnavailable
from app.service.escalations import EscalationService
from tests.db.factories import add_diagnosis, add_journal, add_update
from tests.unit.escalation_fakes import FakeAnnouncer, FakeExpertQuota


class FailingCommitUnitOfWork(SqlAlchemyUnitOfWork):
    def commit(self) -> None:
        raise OperationalError("COMMIT", {}, ConnectionError("lost"))


@pytest.fixture
def user_update(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine) as session:
        journal = add_journal(session)
        update_id = add_update(session, journal["id"])["id"]
        add_diagnosis(
            session,
            update_id,
            status="completed",
            model_effort="high",
            has_dominant_candidate=False,
            recommendation="Water less.",
            clarifying_question="q",
            clarifying_answer="a",
            escalation_offered=True,
        )
        session.commit()
    return journal["user_id"], update_id


def count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(EscalationModel))


def service(
    engine: Engine, quota: FakeExpertQuota, factory: Callable | None = None
) -> EscalationService:
    unit_of_work = factory or partial(SqlAlchemyUnitOfWork, sessionmaker(engine))
    return EscalationService(unit_of_work, quota, FakeAnnouncer())


def test_the_escalation_is_committed_together_with_the_reservation(
    engine: Engine, user_update: tuple[uuid.UUID, uuid.UUID]
):
    user_id, update_id = user_update
    quota = FakeExpertQuota()

    requested = service(engine, quota).request(user_id, update_id, None)

    assert count(engine) == 1
    assert quota.reserved == [requested.escalation.id]
    assert quota.log == ["reserve"]


def test_a_refused_reservation_leaves_no_row(
    engine: Engine, user_update: tuple[uuid.UUID, uuid.UUID]
):
    user_id, update_id = user_update
    quota = FakeExpertQuota()
    quota.exhausted = True

    with pytest.raises(ExpertQuotaExhausted):
        service(engine, quota).request(user_id, update_id, None)

    assert count(engine) == 0
    assert quota.log == ["reserve"]


def test_an_unavailable_reservation_leaves_no_row_and_releases(
    engine: Engine, user_update: tuple[uuid.UUID, uuid.UUID]
):
    user_id, update_id = user_update
    quota = FakeExpertQuota()
    quota.reserve_unavailable = True

    with pytest.raises(ExpertQuotaUnavailable):
        service(engine, quota).request(user_id, update_id, None)

    assert count(engine) == 0
    assert quota.log == ["reserve", "release"]


def test_a_failed_commit_leaves_no_row_and_releases(
    engine: Engine, user_update: tuple[uuid.UUID, uuid.UUID]
):
    user_id, update_id = user_update
    quota = FakeExpertQuota()
    failing = partial(FailingCommitUnitOfWork, sessionmaker(engine))

    with pytest.raises(OperationalError):
        service(engine, quota, failing).request(user_id, update_id, None)

    assert count(engine) == 0
    assert quota.log == ["reserve", "release"]


def test_a_repeated_request_reuses_the_stored_escalation(
    engine: Engine, user_update: tuple[uuid.UUID, uuid.UUID]
):
    user_id, update_id = user_update
    quota = FakeExpertQuota()
    escalations = service(engine, quota)

    first = escalations.request(user_id, update_id, None)
    second = escalations.request(user_id, update_id, None)

    assert second.escalation.id == first.escalation.id
    assert not second.created
    assert count(engine) == 1
    assert quota.reserved == [first.escalation.id]
