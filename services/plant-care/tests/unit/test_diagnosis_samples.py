import contextvars
import threading

import pytest
from growpath_observability import get_correlation_id, set_correlation_id

from app.service.diagnosis_model import (
    DiagnosisRequest,
    InvalidModelAnswer,
    ModelAnswer,
    ModelUnavailable,
)
from app.service.diagnosis_samples import collect_answers
from app.service.dominance import Support
from app.service.model import DiagnosisStatus
from tests.unit.diagnosis_data import make_request
from tests.unit.fake_model import conclusion, question, ranked
from tests.unit.scenario import SAMPLE_TEMPERATURE, USER, Scenario

H, M, L = Support.HIGH, Support.MEDIUM, Support.LOW
PARALLEL_TIMEOUT_SECONDS = 5


class ConcurrentModel:
    """Hands out its script in call order; with `meet`, every call waits for all the others."""

    def __init__(self, *script: ModelAnswer | Exception, meet: int = 0) -> None:
        self._script = list(script)
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(meet, timeout=PARALLEL_TIMEOUT_SECONDS) if meet else None
        self.requests: list[DiagnosisRequest] = []
        self.threads: list[str] = []
        self.correlation_ids: list[str | None] = []

    def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
        if self._barrier is not None:
            self._barrier.wait()
        with self._lock:
            self.requests.append(request)
            self.threads.append(threading.current_thread().name)
            self.correlation_ids.append(get_correlation_id())
            step = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def scenario() -> Scenario:
    return Scenario()


def test_parallel_samples_really_run_at_the_same_time(scenario: Scenario):
    model = ConcurrentModel(conclusion(), meet=3)

    diagnosis = scenario.service(model, samples=3).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DiagnosisStatus.COMPLETED
    assert len(model.requests) == 3
    assert len(set(model.threads)) == 3


def test_an_attempt_spends_one_quota_unit_whatever_the_number_of_samples(scenario: Scenario):
    model = ConcurrentModel(conclusion(), meet=3)

    scenario.service(model, samples=3).run(USER, scenario.update_id)

    assert scenario.quota.reserved == [scenario.reservation_id]
    assert scenario.quota.committed == [scenario.reservation_id]
    assert scenario.quota.released == []


def test_samples_run_at_the_configured_temperature_and_one_sample_at_the_default(
    scenario: Scenario,
):
    many = ConcurrentModel(conclusion(), meet=2)
    scenario.service(many, samples=2).run(USER, scenario.update_id)
    single = ConcurrentModel(conclusion())
    other = Scenario()
    other.service(single).run(USER, other.update_id)

    assert [r.temperature for r in many.requests] == [SAMPLE_TEMPERATURE] * 2
    assert [r.temperature for r in single.requests] == [None]
    assert set(single.threads) == {threading.current_thread().name}


def test_the_worker_threads_keep_the_correlation_id_of_the_request(scenario: Scenario):
    model = ConcurrentModel(conclusion(), meet=2)

    def run() -> None:
        set_correlation_id("corr-1")
        scenario.service(model, samples=2).run(USER, scenario.update_id)

    contextvars.copy_context().run(run)

    assert model.correlation_ids == ["corr-1", "corr-1"]


def test_agreeing_samples_with_a_high_first_candidate_are_dominant(scenario: Scenario):
    model = ConcurrentModel(conclusion(), meet=2)

    summary = scenario.service(model, samples=2).run(USER, scenario.update_id).summary

    assert (summary.status, summary.has_dominant_candidate) == (DiagnosisStatus.COMPLETED, True)


def test_samples_that_disagree_on_the_first_cause_have_no_dominant_candidate(
    scenario: Scenario,
):
    first = conclusion(causes=ranked(("Overwatering", H), ("Root rot", L)))
    second = conclusion(causes=ranked(("Root rot", M), ("Overwatering", L)))
    model = ConcurrentModel(first, second, meet=2)

    diagnosis = scenario.service(model, samples=2).run(USER, scenario.update_id)

    assert diagnosis.summary.has_dominant_candidate is False
    assert [(c.rank, c.cause) for c in diagnosis.candidates] == [
        (1, "Overwatering"),
        (2, "Root rot"),
    ]
    assert diagnosis.summary.escalation_offered is False


def test_the_merged_ranking_follows_the_votes_of_three_samples(scenario: Scenario):
    answers = [
        conclusion(causes=ranked(("Root rot", M), ("Overwatering", H))),
        conclusion(causes=ranked(("Root rot", L), ("Drought", H))),
        conclusion(causes=ranked(("Overwatering", H), ("Root rot", L))),
    ]
    model = ConcurrentModel(*answers, meet=3)

    diagnosis = scenario.service(model, samples=3).run(USER, scenario.update_id)

    assert [c.cause for c in diagnosis.candidates] == ["Root rot", "Overwatering", "Drought"]
    assert diagnosis.summary.has_dominant_candidate is False


@pytest.mark.parametrize("failure", [InvalidModelAnswer("garbage"), ModelUnavailable("full")])
def test_with_one_failed_sample_the_result_is_built_from_the_rest_without_a_dominant_candidate(
    scenario: Scenario, failure: Exception
):
    model = ConcurrentModel(conclusion(), failure, meet=2)

    diagnosis = scenario.service(model, samples=2).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DiagnosisStatus.COMPLETED
    assert diagnosis.summary.has_dominant_candidate is False
    assert diagnosis.summary.recommendation == "Water less."
    assert scenario.quota.committed == [scenario.reservation_id]
    assert scenario.quota.released == []


def test_when_every_sample_fails_the_diagnosis_degrades_and_releases_the_quota(
    scenario: Scenario,
):
    model = ConcurrentModel(ModelUnavailable("down"), InvalidModelAnswer("garbage"), meet=2)

    diagnosis = scenario.service(model, samples=2).run(USER, scenario.update_id)

    assert diagnosis.summary.status is DiagnosisStatus.DEGRADED
    assert scenario.quota.released == [scenario.reservation_id]
    assert scenario.quota.committed == []


def test_the_clarification_round_with_samples_costs_no_further_quota(scenario: Scenario):
    model = ConcurrentModel(question(), question(), conclusion(dominant=False))
    service = scenario.service(model, samples=2)

    first = service.run(USER, scenario.update_id)
    final = service.answer_clarification(USER, scenario.update_id, "No idea")

    assert first.summary.status is DiagnosisStatus.AWAITING_CLARIFICATION
    assert first.summary.clarifying_question == "Are the leaves soft?"
    assert final.summary.status is DiagnosisStatus.COMPLETED
    assert final.summary.escalation_offered is True
    assert len(model.requests) == 4
    assert scenario.quota.reserved == [scenario.reservation_id]
    assert scenario.quota.committed == [scenario.reservation_id]


def test_collect_answers_raises_the_first_failure_when_none_succeeded():
    model = ConcurrentModel(ModelUnavailable("first"))

    with pytest.raises(ModelUnavailable):
        collect_answers(model, make_request(), 2)


def test_a_bug_in_a_call_is_not_swallowed():
    class Broken:
        def diagnose(self, request: DiagnosisRequest) -> ModelAnswer:
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        collect_answers(Broken(), make_request(), 2)
