import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import DiagnosisModel, UpdateModel
from app.repository.diagnoses import SqlAlchemyDiagnosisRepository
from app.repository.updates import SqlAlchemyUpdateRepository
from app.service.errors import Unavailable
from app.service.model import (
    Answer,
    DiagnosisStatus,
    DryDepth,
    EscalationStatus,
    FeedCursor,
    Lighting,
    NewUpdate,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    UpdateRecord,
    WateringMethod,
    YesNo,
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
        "lighting": Lighting.MEDIUM,
        "last_watered_on": date(2026, 11, 9),
        "watering_method": WateringMethod.PARTIAL,
        "pot_diameter_class": PotDiameterClass.FROM_15_TO_20,
        "drainage_holes": Answer.YES,
        "standing_water_over_1h": Answer.NO,
        "dry_depth": DryDepth.ABOUT_2_3_CM,
        "pot_weight_relative": PotWeightRelative.NOTICEABLY_LIGHTER,
        "pot_weight_after_watering_g": Decimal("900.50"),
        "pot_weight_now_g": Decimal("700.00"),
        "repotted_or_moved": YesNo.NO,
        "sensor_reading": Decimal("35.50"),
        "sensor_unit": SensorUnit.VWC_PCT,
        "air_temp_c": Decimal("21.50"),
        "air_rh_pct": Decimal("48.00"),
        "air_measured_at": datetime(2026, 11, 11, 9, 30, tzinfo=UTC),
        "air_temp_min_c": Decimal("18.00"),
        "air_temp_max_c": Decimal("24.00"),
        "air_rh_min_pct": Decimal("40.00"),
        "air_rh_max_pct": Decimal("60.00"),
    }
    return NewUpdate(**{**fields, **overrides})


def test_a_retried_update_id_is_not_stored_twice(session: Session):
    repository = SqlAlchemyUpdateRepository(session)
    journal_id = add_journal(session)["id"]
    update = new_update(journal_id)

    first = repository.add(update)
    retried = repository.add(new_update(journal_id, id=update.id, lighting=Lighting.LOW))

    assert (first, retried) == (True, False)
    assert session.scalars(select(UpdateModel.lighting)).all() == ["medium"]


def test_a_stored_update_round_trips_with_typed_answers(session: Session):
    repository = SqlAlchemyUpdateRepository(session)
    update = new_update(add_journal(session)["id"])
    repository.add(update)

    item = repository.item(update.id)

    assert item is not None
    assert item.update == UpdateRecord(**vars(update), created_at=item.update.created_at)


def test_unanswered_questions_are_stored_as_unknown_and_null(session: Session):
    repository = SqlAlchemyUpdateRepository(session)
    update = NewUpdate(uuid.uuid4(), add_journal(session)["id"], Lighting.LOW)
    repository.add(update)

    item = repository.item(update.id)

    assert item is not None
    stored = item.update
    assert stored.watering_method is WateringMethod.UNKNOWN
    assert stored.drainage_holes is Answer.UNKNOWN
    assert stored.dry_depth is DryDepth.UNKNOWN
    assert stored.repotted_or_moved is None
    assert stored.sensor_unit is None
    assert stored.air_measured_at is None


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


def stamped(session: Session, journal_id: uuid.UUID, created_at: datetime) -> uuid.UUID:
    return add_update(session, journal_id, created_at=created_at)["id"]


def owned(repository: SqlAlchemyUpdateRepository, update_id: uuid.UUID, journal: dict):
    return repository.get_owned(update_id, journal["user_id"])


def test_the_earlier_updates_are_the_journals_own_older_ones_newest_first(session: Session):
    journal = add_journal(session)
    base = datetime(2026, 11, 1, 12, tzinfo=UTC)
    oldest = stamped(session, journal["id"], base)
    middle = stamped(session, journal["id"], base + timedelta(days=1))
    current = stamped(session, journal["id"], base + timedelta(days=2))
    stamped(session, journal["id"], base + timedelta(days=3))
    stamped(session, add_journal(session)["id"], base + timedelta(hours=1))
    repository = SqlAlchemyUpdateRepository(session)

    earlier = repository.earlier_in_journal(owned(repository, current, journal), 10)

    assert [row.id for row in earlier] == [middle, oldest]


def test_the_earlier_updates_are_bounded_to_the_newest_ones(session: Session):
    journal = add_journal(session)
    base = datetime(2026, 11, 1, 12, tzinfo=UTC)
    ids = [stamped(session, journal["id"], base + timedelta(days=day)) for day in range(5)]
    repository = SqlAlchemyUpdateRepository(session)

    earlier = repository.earlier_in_journal(owned(repository, ids[4], journal), 2)

    assert [row.id for row in earlier] == [ids[3], ids[2]]


def test_updates_with_the_same_instant_are_ordered_by_id(session: Session):
    journal = add_journal(session)
    moment = datetime(2026, 11, 1, 12, tzinfo=UTC)
    ids = sorted(stamped(session, journal["id"], moment) for _ in range(3))
    repository = SqlAlchemyUpdateRepository(session)

    earlier = repository.earlier_in_journal(owned(repository, ids[2], journal), 10)

    assert [row.id for row in earlier] == [ids[1], ids[0]]


def test_a_database_failure_is_reported_as_unavailable(session: Session):
    journal = add_journal(session)
    update_id = stamped(session, journal["id"], datetime(2026, 11, 1, tzinfo=UTC))
    update = owned(SqlAlchemyUpdateRepository(session), update_id, journal)
    broken = SqlAlchemyUpdateRepository(Failing())

    with pytest.raises(Unavailable):
        broken.earlier_in_journal(update, 3)


class Failing:
    def execute(self, *args, **kwargs):
        raise OperationalError("select", {}, Exception("connection lost"))
