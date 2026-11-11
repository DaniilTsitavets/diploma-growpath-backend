import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiagnosisModel, UpdateModel
from app.repository.diagnoses import SqlAlchemyDiagnosisRepository
from app.repository.updates import SqlAlchemyUpdateRepository
from app.service.model import (
    DiagnosisStatus,
    Drainage,
    EscalationStatus,
    FeedCursor,
    FingerTest,
    Lighting,
    NewUpdate,
    PotWeight,
)
from tests.db.factories import (
    add_diagnosis,
    add_escalation,
    add_journal,
    add_update,
    db_now,
)


def new_update(journal_id: uuid.UUID, **overrides) -> NewUpdate:
    fields = {
        "id": uuid.uuid4(),
        "journal_id": journal_id,
        "last_watered_on": None,
        "watering_interval_days": 7,
        "drainage": Drainage.GOOD,
        "lighting": Lighting.MEDIUM,
        "finger_test": FingerTest.DRY,
        "pot_weight": PotWeight.LIGHTER,
        "sensor_reading": Decimal("35.50"),
    }
    return NewUpdate(**{**fields, **overrides})


def test_a_retried_update_id_is_not_stored_twice(session: Session):
    repository = SqlAlchemyUpdateRepository(session)
    journal_id = add_journal(session)["id"]
    update = new_update(journal_id)

    first = repository.add(update)
    retried = repository.add(new_update(journal_id, id=update.id, drainage=Drainage.POOR))

    assert (first, retried) == (True, False)
    assert session.scalars(select(UpdateModel.drainage)).all() == ["good"]


def test_a_stored_update_round_trips_with_typed_answers(session: Session):
    repository = SqlAlchemyUpdateRepository(session)
    update = new_update(add_journal(session)["id"])
    repository.add(update)

    item = repository.item(update.id)

    assert item is not None
    stored = item.update
    assert (stored.drainage, stored.lighting, stored.finger_test, stored.pot_weight) == (
        Drainage.GOOD,
        Lighting.MEDIUM,
        FingerTest.DRY,
        PotWeight.LIGHTER,
    )
    assert stored.sensor_reading == Decimal("35.50")
    assert stored.journal_id == update.journal_id


def test_a_missing_update_is_none(session: Session):
    assert SqlAlchemyUpdateRepository(session).item(uuid.uuid4()) is None


def test_an_update_is_owned_through_its_journal(session: Session):
    journal = add_journal(session)
    update_id = add_update(session, journal["id"])["id"]
    repository = SqlAlchemyUpdateRepository(session)

    assert repository.get_owned(update_id, journal["user_id"]) is not None
    assert repository.get_owned(update_id, uuid.uuid4()) is None


def test_a_pending_diagnosis_is_created_once_per_update(session: Session):
    repository = SqlAlchemyDiagnosisRepository(session)
    update_id = add_update(session)["id"]
    reservation_id = uuid.uuid4()

    first = repository.add_pending(update_id, reservation_id)
    again = repository.add_pending(update_id, uuid.uuid4())

    row = session.execute(select(DiagnosisModel)).scalar_one()
    assert (first, again) == (True, False)
    assert (row.status, row.ai_reservation_id, row.model_effort) == (
        DiagnosisStatus.PENDING.value,
        reservation_id,
        None,
    )


def test_the_feed_is_newest_first_and_only_of_one_journal(session: Session):
    journal_id = add_journal(session)["id"]
    now = db_now(session)
    old = add_update(session, journal_id, created_at=now - timedelta(hours=2))["id"]
    new = add_update(session, journal_id, created_at=now)["id"]
    add_update(session)

    feed = SqlAlchemyUpdateRepository(session).feed(journal_id, 10, None)

    assert [item.update.id for item in feed] == [new, old]


def test_updates_created_at_the_same_instant_are_ordered_by_id(session: Session):
    journal_id = add_journal(session)["id"]
    now = db_now(session)
    ids = sorted(add_update(session, journal_id, created_at=now)["id"] for _ in range(3))

    feed = SqlAlchemyUpdateRepository(session).feed(journal_id, 10, None)

    assert [item.update.id for item in feed] == ids[::-1]


def test_keyset_pages_cover_the_feed_without_gaps_or_repeats(session: Session):
    journal_id = add_journal(session)["id"]
    now = db_now(session)
    for index in range(5):
        add_update(session, journal_id, created_at=now - timedelta(hours=index // 2))
    repository = SqlAlchemyUpdateRepository(session)

    seen, cursor = [], None
    while page := repository.feed(journal_id, 2, cursor):
        seen += [item.update.id for item in page]
        last = page[-1].update
        cursor = FeedCursor(last.created_at, last.id)

    assert len(seen) == len(set(seen)) == 5
    assert seen == [item.update.id for item in repository.feed(journal_id, 10, None)]


def test_the_feed_item_carries_the_diagnosis_and_the_latest_escalation(session: Session):
    journal_id = add_journal(session)["id"]
    update_id = add_update(session, journal_id)["id"]
    add_diagnosis(
        session,
        update_id,
        status="completed",
        model_effort="high",
        recommendation="Repot",
        has_dominant_candidate=True,
    )
    now = db_now(session)
    add_escalation(session, update_id, status="cancelled", created_at=now - timedelta(hours=5))
    latest = add_escalation(session, update_id, created_at=now)

    (item,) = SqlAlchemyUpdateRepository(session).feed(journal_id, 10, None)

    assert item.diagnosis is not None
    assert (item.diagnosis.status, item.diagnosis.recommendation) == (
        DiagnosisStatus.COMPLETED,
        "Repot",
    )
    assert item.escalation is not None
    assert (item.escalation.id, item.escalation.status) == (
        latest["id"],
        EscalationStatus.REQUESTED,
    )


def test_an_update_without_diagnosis_or_escalation_is_still_in_the_feed(session: Session):
    journal_id = add_journal(session)["id"]
    add_update(session, journal_id)

    (item,) = SqlAlchemyUpdateRepository(session).feed(journal_id, 10, None)

    assert (item.diagnosis, item.escalation) == (None, None)


def test_an_answered_escalation_shows_the_answer(session: Session):
    journal_id = add_journal(session)["id"]
    update_id = add_update(session, journal_id)["id"]
    add_escalation(
        session,
        update_id,
        status="answered",
        ticket_id=uuid.uuid4(),
        answer_text="Water less",
        answered_at=db_now(session),
    )

    (item,) = SqlAlchemyUpdateRepository(session).feed(journal_id, 10, None)

    assert item.escalation is not None
    assert (item.escalation.status, item.escalation.answer_text) == (
        EscalationStatus.ANSWERED,
        "Water less",
    )
