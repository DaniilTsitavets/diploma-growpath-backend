import uuid

import pytest

from app.service.errors import (
    EscalationNotFound,
    EscalationNotOffered,
    ExpertQuotaExhausted,
    ExpertQuotaUnavailable,
    ExpertReservationCommitted,
    InvalidRequest,
    UpdateNotFound,
)
from app.service.escalations import QUESTION_LIMIT, EscalationService
from app.service.model import EscalationStatus
from tests.unit.escalation_fakes import (
    EscalationUnitOfWork,
    FakeAnnouncer,
    FakeExpertQuota,
)

USER = uuid.uuid4()


class Setup:
    def __init__(self, announce: bool = True, **diagnosis: object) -> None:
        self.uow = EscalationUnitOfWork()
        self.quota = FakeExpertQuota()
        self.announcer = FakeAnnouncer()
        journal = self.uow.journals.seed(USER)
        self.update_id = self.uow.updates.seed(journal.id).id
        fields = {
            "escalation_offered": True,
            "clarifying_answer": "yes",
            "clarifying_question": "q",
        }
        self.uow.diagnoses.seed(self.update_id, **{**fields, **diagnosis})
        self.service = EscalationService(
            lambda: self.uow, self.quota, self.announcer if announce else None
        )


@pytest.fixture
def setup() -> Setup:
    return Setup()


def test_an_offered_escalation_is_stored_and_reserved_in_one_transaction(setup: Setup):
    requested = setup.service.request(USER, setup.update_id, None)

    escalation = requested.escalation
    assert requested.created
    assert escalation.status is EscalationStatus.REQUESTED
    assert escalation.user_question is None
    assert setup.quota.reserved == [escalation.id]
    assert setup.quota.users == [USER]
    assert setup.uow.escalations.rows[escalation.id] == escalation
    assert setup.quota.released == []


def test_the_row_is_inserted_before_the_reservation_is_asked_for(setup: Setup):
    inserted_when_reserved: list[bool] = []
    reserve = setup.quota.reserve_expert

    def spy(escalation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        inserted_when_reserved.append(escalation_id in setup.uow.escalations.rows)
        reserve(escalation_id, user_id)

    setup.quota.reserve_expert = spy

    setup.service.request(USER, setup.update_id, None)

    assert inserted_when_reserved == [True]


def test_a_manual_escalation_needs_no_offer_and_carries_the_question():
    setup = Setup(escalation_offered=False, clarifying_answer=None, clarifying_question=None)

    requested = setup.service.request(USER, setup.update_id, "  Why are the tips brown?  ")

    assert requested.created
    assert requested.escalation.user_question == "Why are the tips brown?"


def test_a_manual_escalation_is_allowed_after_the_offer_was_declined():
    setup = Setup(escalation_declined=True)

    assert setup.service.request(USER, setup.update_id, "Still unsure").created


@pytest.mark.parametrize(
    "diagnosis",
    [{"escalation_offered": False}, {"escalation_declined": True}],
    ids=["not", "declined"],
)
def test_without_a_question_the_offer_is_required(diagnosis: dict):
    setup = Setup(**diagnosis)

    with pytest.raises(EscalationNotOffered):
        setup.service.request(USER, setup.update_id, None)

    assert setup.quota.reserved == []
    assert setup.uow.escalations.rows == {}


def test_without_a_diagnosis_and_a_question_the_request_is_not_offered(setup: Setup):
    setup.uow.diagnoses.states.clear()

    with pytest.raises(EscalationNotOffered):
        setup.service.request(USER, setup.update_id, None)


@pytest.mark.parametrize("text", ["", "   ", "a" * (QUESTION_LIMIT + 1), "bad\x00char"])
def test_an_unusable_question_is_rejected_before_anything_happens(setup: Setup, text: str):
    with pytest.raises(InvalidRequest):
        setup.service.request(USER, setup.update_id, text)

    assert setup.quota.log == []


def test_a_foreign_or_unknown_update_is_not_found(setup: Setup):
    with pytest.raises(UpdateNotFound):
        setup.service.request(uuid.uuid4(), setup.update_id, "q")
    with pytest.raises(UpdateNotFound):
        setup.service.request(USER, uuid.uuid4(), "q")
    with pytest.raises(UpdateNotFound):
        setup.service.get(uuid.uuid4(), setup.update_id)


def test_an_exhausted_quota_rolls_the_row_back_and_releases_nothing(setup: Setup):
    setup.quota.exhausted = True

    with pytest.raises(ExpertQuotaExhausted):
        setup.service.request(USER, setup.update_id, None)

    assert setup.uow.escalations.rows == {}
    assert setup.quota.log == ["reserve"]
    assert setup.announcer.announced == []


def test_an_unavailable_quota_rolls_back_and_releases_the_possible_reservation(setup: Setup):
    setup.quota.reserve_unavailable = True

    with pytest.raises(ExpertQuotaUnavailable):
        setup.service.request(USER, setup.update_id, None)

    assert setup.uow.escalations.rows == {}
    assert setup.quota.log == ["reserve", "release"]
    assert len(setup.quota.released) == 1
    assert setup.announcer.announced == []


@pytest.mark.parametrize("failure", [ExpertQuotaUnavailable(), ExpertReservationCommitted()])
def test_a_failing_release_does_not_hide_the_original_error(setup: Setup, failure: Exception):
    setup.quota.reserve_unavailable = True
    setup.quota.release_fails_with = failure

    with pytest.raises(ExpertQuotaUnavailable):
        setup.service.request(USER, setup.update_id, None)


def test_a_failed_commit_releases_the_reservation_and_propagates(setup: Setup):
    setup.uow.fail_commit_with = ConnectionError("commit lost")

    with pytest.raises(ConnectionError):
        setup.service.request(USER, setup.update_id, None)

    assert setup.uow.escalations.rows == {}
    assert setup.quota.log == ["reserve", "release"]
    assert setup.quota.released == setup.quota.reserved


def test_a_repeated_request_returns_the_active_escalation_and_announces_it_again(setup: Setup):
    first = setup.service.request(USER, setup.update_id, None)

    second = setup.service.request(USER, setup.update_id, None)

    assert not second.created
    assert second.escalation.id == first.escalation.id
    assert setup.quota.reserved == [first.escalation.id]
    assert [e.id for e in setup.announcer.announced] == [first.escalation.id] * 2


@pytest.mark.parametrize(
    "status", [EscalationStatus.TICKET_OPEN, EscalationStatus.ANSWERED], ids=lambda s: s.value
)
def test_a_repeated_request_for_a_confirmed_escalation_is_not_announced(
    setup: Setup, status: EscalationStatus
):
    first = setup.service.request(USER, setup.update_id, None)
    setup.announcer.announced.clear()
    setup.uow.escalations.change(first.escalation.id, status=status)

    second = setup.service.request(USER, setup.update_id, None)

    assert not second.created
    assert second.escalation.status is status
    assert setup.announcer.announced == []


def test_a_repeated_request_works_without_the_offer_once_the_escalation_exists(setup: Setup):
    first = setup.service.request(USER, setup.update_id, None)
    setup.uow.diagnoses.seed(setup.update_id, escalation_offered=False)

    assert setup.service.request(USER, setup.update_id, None).escalation.id == first.escalation.id


def test_the_update_can_be_escalated_again_after_a_cancellation(setup: Setup):
    first = setup.service.request(USER, setup.update_id, None)
    setup.uow.escalations.change(first.escalation.id, status=EscalationStatus.CANCELLED)

    second = setup.service.request(USER, setup.update_id, None)

    assert second.created
    assert second.escalation.id != first.escalation.id


def test_losing_the_race_returns_the_winner_without_a_second_reservation(setup: Setup):
    winner = setup.uow.escalations.seed(setup.update_id)
    real_get_active = setup.uow.escalations.get_active
    state = {"blind": True}

    def get_active_once_blind(update_id: uuid.UUID):
        if state["blind"]:
            state["blind"] = False
            return None
        return real_get_active(update_id)

    setup.uow.escalations.get_active = get_active_once_blind

    requested = setup.service.request(USER, setup.update_id, None)

    assert not requested.created
    assert requested.escalation.id == winner.id
    assert setup.quota.log == []


def test_the_service_works_without_an_announcer():
    setup = Setup(announce=False)

    assert setup.service.request(USER, setup.update_id, None).created
    assert setup.service.request(USER, setup.update_id, None).created is False


def test_get_returns_the_latest_escalation(setup: Setup):
    first = setup.uow.escalations.seed(setup.update_id, status=EscalationStatus.CANCELLED)
    second = setup.uow.escalations.seed(setup.update_id)

    assert setup.service.get(USER, setup.update_id).id == second.id
    assert first.id != second.id


def test_get_without_an_escalation_is_not_found(setup: Setup):
    with pytest.raises(EscalationNotFound):
        setup.service.get(USER, setup.update_id)
