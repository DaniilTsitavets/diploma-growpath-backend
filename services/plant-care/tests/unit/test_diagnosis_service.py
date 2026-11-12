import uuid
from dataclasses import fields

import pytest

from app.service.diagnosis import validate_clarification_answer
from app.service.diagnosis_model import InvalidModelAnswer, ModelUnavailable, PhotoUnreadable
from app.service.diagnosis_prompt import FALLBACK_RECOMMENDATION
from app.service.dominance import Support
from app.service.errors import (
    AiQuotaExhausted,
    AiQuotaUnavailable,
    DiagnosisConflict,
    InvalidRequest,
    PhotosMissing,
    ReservationUnusable,
    UpdateNotFound,
)
from app.service.model import CandidateRecord, DiagnosisStatus, ModelEffort, PhotoKind, Tier
from tests.unit.fake_model import FakeModel, conclusion, question, ranked
from tests.unit.fakes import FakeAiQuota
from tests.unit.scenario import USER, Scenario

PENDING = DiagnosisStatus.PENDING
AWAITING = DiagnosisStatus.AWAITING_CLARIFICATION
COMPLETED = DiagnosisStatus.COMPLETED
DEGRADED = DiagnosisStatus.DEGRADED


@pytest.fixture
def scenario() -> Scenario:
    return Scenario()


def test_a_first_attempt_completes_with_candidates_and_commits_the_reserved_quota(
    scenario: Scenario,
):
    model = FakeModel(conclusion(dominant=True))

    diagnosis = scenario.service(model).run(USER, scenario.update_id)

    summary = diagnosis.summary
    assert (summary.status, summary.model_effort) == (COMPLETED, "high")
    assert summary.has_dominant_candidate is True
    assert summary.recommendation == "Water less."
    assert summary.escalation_offered is False
    assert [(c.rank, c.cause) for c in diagnosis.candidates] == [
        (1, "Overwatering"),
        (2, "Poor drainage"),
    ]
    assert diagnosis.ai_reservation_id == scenario.reservation_id
    assert scenario.quota.committed == [scenario.reservation_id]
    assert scenario.quota.released == []
    assert len(scenario.quota.reserved) == 1


def test_the_model_receives_the_species_photos_effort_and_no_clarification_yet(
    scenario: Scenario,
):
    model = FakeModel(conclusion())

    scenario.service(model).run(USER, scenario.update_id)

    (request,) = model.requests
    assert request.species == "Basil"
    assert {photo.kind for photo in request.photos} == {PhotoKind.OVERVIEW, PhotoKind.SOIL}
    assert request.effort is ModelEffort.HIGH
    assert (request.clarifying_question, request.clarifying_answer) == (None, None)


def test_the_starter_tier_and_a_failed_tier_lookup_use_the_medium_effort():
    for tier in (Tier.STARTER, None):
        scenario = Scenario(tier)

        diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

        assert diagnosis.summary.model_effort == "medium"


def test_a_clarifying_question_is_stored_and_the_quota_is_committed(scenario: Scenario):
    diagnosis = scenario.service(FakeModel(question(roots_photo=True))).run(
        USER, scenario.update_id
    )

    summary = diagnosis.summary
    assert summary.status is AWAITING
    assert summary.clarifying_question == "Are the leaves soft?"
    assert summary.roots_photo_requested is True
    assert summary.recommendation is None
    assert summary.has_dominant_candidate is False
    assert scenario.quota.committed == [scenario.reservation_id]


def test_the_application_not_the_model_decides_that_a_candidate_dominates(scenario: Scenario):
    strong = conclusion(causes=ranked(("Overwatering", Support.HIGH), ("Drought", Support.MEDIUM)))

    diagnosis = scenario.service(FakeModel(strong)).run(USER, scenario.update_id)

    assert diagnosis.summary.has_dominant_candidate is False


def test_the_support_labels_are_not_stored(scenario: Scenario):
    diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert {field.name for field in fields(CandidateRecord)} == {"rank", "cause", "rationale"}
    assert all(not hasattr(candidate, "support") for candidate in diagnosis.candidates)


def test_the_answer_re_evaluates_on_the_same_reservation_without_spending_quota(
    scenario: Scenario,
):
    model = FakeModel(question(), conclusion(dominant=True))
    service = scenario.service(model)
    service.run(USER, scenario.update_id)

    diagnosis = service.answer_clarification(USER, scenario.update_id, "  Yes, very soft ")

    assert diagnosis.summary.status is COMPLETED
    assert diagnosis.summary.clarifying_answer == "Yes, very soft"
    assert diagnosis.summary.escalation_offered is False
    assert diagnosis.ai_reservation_id == scenario.reservation_id
    assert scenario.quota.committed == [scenario.reservation_id]
    assert scenario.quota.reserved == [scenario.reservation_id]
    second = model.requests[1]
    assert (second.clarifying_question, second.clarifying_answer) == (
        "Are the leaves soft?",
        "Yes, very soft",
    )
    assert second.effort is ModelEffort.HIGH


def test_without_a_dominant_candidate_after_the_clarification_the_expert_is_offered(
    scenario: Scenario,
):
    service = scenario.service(FakeModel(question(), conclusion(dominant=False)))
    service.run(USER, scenario.update_id)

    diagnosis = service.answer_clarification(USER, scenario.update_id, "No idea")

    assert diagnosis.summary.status is COMPLETED
    assert diagnosis.summary.has_dominant_candidate is False
    assert diagnosis.summary.escalation_offered is True


def test_without_a_dominant_candidate_and_without_a_question_no_expert_is_offered(
    scenario: Scenario,
):
    diagnosis = scenario.service(FakeModel(conclusion(dominant=False))).run(
        USER, scenario.update_id
    )

    assert diagnosis.summary.escalation_offered is False


def test_a_second_question_is_not_allowed_and_the_re_evaluation_degrades(scenario: Scenario):
    service = scenario.service(FakeModel(question(), question()))
    service.run(USER, scenario.update_id)

    diagnosis = service.answer_clarification(USER, scenario.update_id, "Yes")

    assert diagnosis.summary.status is DEGRADED
    assert diagnosis.summary.clarifying_answer == "Yes"
    assert scenario.quota.released == []


@pytest.mark.parametrize(
    "failure",
    [ModelUnavailable("timeout"), InvalidModelAnswer("garbage"), PhotoUnreadable("gone")],
)
def test_a_model_failure_releases_the_reservation_and_degrades_with_a_fallback(
    scenario: Scenario, failure: Exception
):
    diagnosis = scenario.service(FakeModel(failure)).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DEGRADED
    assert diagnosis.summary.recommendation == FALLBACK_RECOMMENDATION
    assert scenario.quota.released == [scenario.reservation_id]
    assert scenario.quota.committed == []


def test_a_retry_after_degrading_reserves_a_new_id_and_commits_that_one(scenario: Scenario):
    model = FakeModel(ModelUnavailable("timeout"), conclusion())
    service = scenario.service(model)
    service.run(USER, scenario.update_id)

    diagnosis = service.run(USER, scenario.update_id)

    new_id = diagnosis.ai_reservation_id
    assert diagnosis.summary.status is COMPLETED
    assert new_id != scenario.reservation_id
    assert scenario.quota.reserved == [scenario.reservation_id, new_id]
    assert scenario.quota.committed == [new_id]
    assert diagnosis.summary.recommendation == "Water less."


def test_a_released_or_expired_reservation_is_replaced_by_a_new_one(scenario: Scenario):
    scenario.quota.unusable.add(scenario.reservation_id)

    diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert diagnosis.ai_reservation_id != scenario.reservation_id
    assert scenario.quota.committed == [diagnosis.ai_reservation_id]


def test_a_pending_attempt_never_reserved_because_entitlement_was_down_reserves_now(
    scenario: Scenario,
):
    scenario.quota.reserved.clear()

    scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert scenario.quota.reserved == [scenario.reservation_id]


def test_an_exhausted_quota_blocks_the_attempt_and_leaves_the_diagnosis_untouched(
    scenario: Scenario,
):
    scenario.quota.reserved.clear()
    scenario.quota.exhausted = True
    model = FakeModel(conclusion())

    with pytest.raises(AiQuotaExhausted):
        scenario.service(model).run(USER, scenario.update_id)

    assert model.requests == []
    assert scenario.row.summary.status is PENDING
    assert scenario.row.summary.model_effort is None


def test_an_unavailable_entitlement_fails_the_request_and_leaves_the_diagnosis_pending(
    scenario: Scenario,
):
    scenario.quota.unavailable = True
    model = FakeModel(conclusion())

    with pytest.raises(AiQuotaUnavailable):
        scenario.service(model).run(USER, scenario.update_id)

    assert model.requests == []
    assert scenario.row.summary.status is PENDING


def test_an_attempt_that_is_already_running_is_not_started_twice(scenario: Scenario):
    scenario.seed(model_effort="high")
    model = FakeModel(conclusion())

    with pytest.raises(DiagnosisConflict):
        scenario.service(model).run(USER, scenario.update_id)

    assert model.requests == []
    assert scenario.quota.released == []


def test_replay_of_a_live_reservation_means_the_attempt_is_running(scenario: Scenario):
    scenario.seed(model_effort="high")

    with pytest.raises(DiagnosisConflict, match="running"):
        scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert scenario.row.ai_reservation_id == scenario.reservation_id
    assert scenario.quota.reserved == [scenario.reservation_id]


def test_replay_of_a_released_or_expired_reservation_lets_the_next_attempt_take_over(
    scenario: Scenario,
):
    scenario.seed(model_effort="high")
    scenario.quota.unusable.add(scenario.reservation_id)

    diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert diagnosis.summary.status is COMPLETED
    assert diagnosis.ai_reservation_id != scenario.reservation_id
    assert scenario.quota.committed == [diagnosis.ai_reservation_id]


def test_replay_of_a_never_made_reservation_creates_it_and_the_attempt_continues(
    scenario: Scenario,
):
    scenario.quota.reserved.clear()

    diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert diagnosis.ai_reservation_id == scenario.reservation_id
    assert scenario.quota.reserved == [scenario.reservation_id]
    assert scenario.quota.committed == [scenario.reservation_id]


def test_a_committed_reservation_of_a_re_evaluation_is_not_reserved_again(scenario: Scenario):
    scenario.seed(
        model_effort="high",
        clarifying_question="Q?",
        clarifying_answer="A",
    )
    scenario.quota.reserved.clear()
    scenario.quota.unavailable = True
    model = FakeModel(conclusion(dominant=False))

    diagnosis = scenario.service(model).run(USER, scenario.update_id)

    assert diagnosis.summary.status is COMPLETED
    assert diagnosis.summary.escalation_offered is True
    assert diagnosis.ai_reservation_id == scenario.reservation_id
    assert scenario.quota.reserved == []
    assert scenario.quota.committed == []
    assert scenario.quota.released == []


def test_a_failing_resumed_re_evaluation_degrades_without_touching_the_quota(scenario: Scenario):
    scenario.seed(model_effort="high", clarifying_question="Q?", clarifying_answer="A")

    diagnosis = scenario.service(FakeModel(ModelUnavailable("down"))).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DEGRADED
    assert scenario.quota.released == []


@pytest.mark.parametrize("state", [{}, {"model_effort": "high"}, {"status": DEGRADED}])
def test_an_unavailable_entitlement_writes_nothing_and_never_degrades(
    scenario: Scenario, state: dict
):
    if state.get("status") is DEGRADED:
        state = {**state, "model_effort": "high", "recommendation": "fallback"}
    scenario.seed(**state)
    before = scenario.row
    scenario.quota.unavailable = True
    model = FakeModel(conclusion())

    with pytest.raises(AiQuotaUnavailable):
        scenario.service(model).run(USER, scenario.update_id)

    assert scenario.row == before
    assert model.requests == []
    assert scenario.uow.commits == 0
    assert scenario.quota.released == []


def test_the_attempt_after_an_entitlement_outage_replays_the_same_reservation(scenario: Scenario):
    service = scenario.service(FakeModel(conclusion()))
    scenario.quota.unavailable = True
    with pytest.raises(AiQuotaUnavailable):
        service.run(USER, scenario.update_id)
    scenario.quota.unavailable = False

    diagnosis = service.run(USER, scenario.update_id)

    assert diagnosis.ai_reservation_id == scenario.reservation_id
    assert diagnosis.summary.status is COMPLETED


def test_the_tier_is_not_looked_up_while_entitlement_is_unavailable(scenario: Scenario):
    looked_up = []
    scenario.tiers.tier_of = looked_up.append
    scenario.quota.unavailable = True

    with pytest.raises(AiQuotaUnavailable):
        scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert looked_up == []


def test_losing_the_claim_of_a_retry_releases_the_new_reservation(scenario: Scenario):
    scenario.seed(status=DEGRADED, model_effort="high", recommendation="fallback")
    rival = uuid.uuid4()

    class Racing(FakeAiQuota):
        def reserve(self, reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
            super().reserve(reservation_id, user_id)
            scenario.uow.diagnoses.seed(
                scenario.update_id, rival, model_effort="high", status=PENDING
            )

    scenario.quota = racing = Racing()
    model = FakeModel(conclusion())

    with pytest.raises(DiagnosisConflict):
        scenario.service(model).run(USER, scenario.update_id)

    assert model.requests == []
    assert len(racing.released) == 1
    assert racing.released[0] != rival
    assert scenario.row.ai_reservation_id == rival


def test_an_attempt_that_lost_the_diagnosis_while_the_model_ran_releases_its_reservation(
    scenario: Scenario,
):
    rival = uuid.uuid4()
    model = FakeModel(conclusion())
    model.during_call = lambda: scenario.uow.diagnoses.seed(
        scenario.update_id, rival, model_effort="high"
    )

    with pytest.raises(DiagnosisConflict):
        scenario.service(model).run(USER, scenario.update_id)

    assert scenario.quota.released == [scenario.reservation_id]
    assert scenario.quota.committed == []
    assert scenario.row.summary.status is PENDING


def test_a_failing_attempt_that_lost_the_diagnosis_still_releases_its_reservation(
    scenario: Scenario,
):
    model = FakeModel(ModelUnavailable("timeout"))
    model.during_call = lambda: scenario.uow.diagnoses.seed(
        scenario.update_id, uuid.uuid4(), model_effort="high"
    )

    with pytest.raises(DiagnosisConflict):
        scenario.service(model).run(USER, scenario.update_id)

    assert scenario.quota.released == [scenario.reservation_id]
    assert scenario.row.summary.status is PENDING


@pytest.mark.parametrize("failure", [AiQuotaUnavailable(), ReservationUnusable()])
def test_a_failed_commit_keeps_the_diagnosis_and_leaves_it_unpaid(
    scenario: Scenario, failure: Exception
):
    scenario.quota.commit_fails_with = failure

    diagnosis = scenario.service(FakeModel(conclusion())).run(USER, scenario.update_id)

    assert diagnosis.summary.status is COMPLETED


def test_a_failed_release_still_degrades_the_diagnosis(scenario: Scenario):
    scenario.quota.release_unavailable = True

    diagnosis = scenario.service(FakeModel(ModelUnavailable("x"))).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DEGRADED


def test_the_required_photos_must_exist_before_anything_is_reserved(scenario: Scenario):
    scenario.uow.photos.rows.clear()
    scenario.quota.reserved.clear()
    scenario.uow.photos.add(scenario.update_id, PhotoKind.OVERVIEW, "key/overview")
    model = FakeModel(conclusion())

    with pytest.raises(PhotosMissing, match="soil"):
        scenario.service(model).run(USER, scenario.update_id)

    assert scenario.quota.reserved == []
    assert model.requests == []


@pytest.mark.parametrize("status", [COMPLETED, AWAITING])
def test_a_finished_or_waiting_diagnosis_is_returned_without_a_new_attempt(
    scenario: Scenario, status: DiagnosisStatus
):
    scenario.seed(status=status, model_effort="high", clarifying_question="Q?")
    model = FakeModel(conclusion())

    diagnosis = scenario.service(model).run(USER, scenario.update_id)

    assert diagnosis.summary.status is status
    assert model.requests == []


def test_another_users_update_is_not_found(scenario: Scenario):
    service = scenario.service(FakeModel(conclusion()))

    for call in (service.run, service.get, service.decline_escalation):
        with pytest.raises(UpdateNotFound):
            call(uuid.uuid4(), scenario.update_id)
    with pytest.raises(UpdateNotFound):
        service.answer_clarification(uuid.uuid4(), scenario.update_id, "yes")


def test_an_answer_is_accepted_only_while_one_is_awaited(scenario: Scenario):
    service = scenario.service(FakeModel(conclusion()))

    with pytest.raises(DiagnosisConflict, match="not waiting"):
        service.answer_clarification(USER, scenario.update_id, "yes")


def test_the_answer_is_validated_before_anything_changes(scenario: Scenario):
    scenario.seed(status=AWAITING, model_effort="high", clarifying_question="Q?")

    with pytest.raises(InvalidRequest):
        scenario.service(FakeModel(conclusion())).answer_clarification(
            USER, scenario.update_id, "   "
        )

    assert scenario.row.summary.clarifying_answer is None


def test_the_clarification_can_be_answered_only_once(scenario: Scenario):
    service = scenario.service(FakeModel(question(), conclusion()))
    service.run(USER, scenario.update_id)
    service.answer_clarification(USER, scenario.update_id, "yes")

    with pytest.raises(DiagnosisConflict):
        service.answer_clarification(USER, scenario.update_id, "yes again")


def test_declining_the_offered_expert_is_recorded_and_repeatable(scenario: Scenario):
    service = scenario.service(FakeModel(question(), conclusion(dominant=False)))
    service.run(USER, scenario.update_id)
    service.answer_clarification(USER, scenario.update_id, "no idea")

    first = service.decline_escalation(USER, scenario.update_id)
    again = service.decline_escalation(USER, scenario.update_id)

    assert first.summary.escalation_declined is True
    assert again.summary.escalation_declined is True


def test_declining_when_no_expert_was_offered_is_a_conflict(scenario: Scenario):
    service = scenario.service(FakeModel(conclusion()))
    service.run(USER, scenario.update_id)

    with pytest.raises(DiagnosisConflict, match="no expert"):
        service.decline_escalation(USER, scenario.update_id)


def test_get_returns_the_stored_diagnosis(scenario: Scenario):
    diagnosis = scenario.service(FakeModel(conclusion())).get(USER, scenario.update_id)

    assert diagnosis.summary.status is PENDING


@pytest.mark.parametrize(
    "text",
    ["", "   ", "x" * 1001, "bad\x00char", "bell\x07"],
)
def test_invalid_answers_are_rejected(text: str):
    with pytest.raises(InvalidRequest):
        validate_clarification_answer(text)


def test_a_valid_answer_is_normalised_and_keeps_line_breaks():
    assert validate_clarification_answer("  é\nok  ") == "é\nok"
    assert len(validate_clarification_answer("x" * 1000)) == 1000
