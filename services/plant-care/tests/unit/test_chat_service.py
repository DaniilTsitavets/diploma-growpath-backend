import logging
import uuid

import pytest

from app.service.chat import QUESTION_LIMIT, validate_question
from app.service.chat_answer import NOT_IN_KB_TEXT
from app.service.chat_model import ChatOutcome, ModelChatAnswer
from app.service.cursor import decode_cursor
from app.service.diagnosis_model import InvalidModelAnswer, ModelUnavailable
from app.service.errors import (
    AiQuotaExhausted,
    AiQuotaUnavailable,
    ChatTurnNotFound,
    InvalidCursor,
    InvalidRequest,
    JournalNotFound,
    ReservationUnusable,
)
from app.service.model import ModelEffort, Tier
from tests.unit.chat_fakes import ChatScenario, FakeChatModel, answered
from tests.unit.scenario import USER


@pytest.fixture
def scenario() -> ChatScenario:
    return ChatScenario()


def test_an_answer_is_stored_with_its_citations_and_the_turn_is_created(scenario: ChatScenario):
    model = FakeChatModel(answered("S2", "S1", text="Water when the top layer is dry."))
    service = scenario.service(model)

    reply = scenario.ask(service, "How often should I water?")

    turn = reply.turn
    assert reply.created and reply.related == []
    assert turn is not None
    assert (turn.outcome, turn.answer) == (ChatOutcome.ANSWERED, "Water when the top layer is dry.")
    assert turn.question == "How often should I water?"
    assert [(s.rank, s.section_id) for s in turn.sources] == [
        (1, scenario.sections[1].id),
        (2, scenario.sections[0].id),
    ]
    assert scenario.uow.chat_turns.turns[turn.id] == turn


def test_the_reservation_is_made_before_the_model_and_committed_after_the_turn_is_saved(
    scenario: ChatScenario,
):
    service = scenario.service(FakeChatModel(answered("S1")))

    scenario.ask(service)

    assert scenario.events == ["reserve", "db-commit", "commit"]
    (stored,) = scenario.uow.chat_turns.added
    assert scenario.quota.reserved == scenario.quota.committed == [stored.ai_reservation_id]
    assert scenario.quota.released == []


def test_every_attempt_uses_a_new_reservation_id(scenario: ChatScenario):
    service = scenario.service(FakeChatModel())

    scenario.ask(service)
    scenario.ask(service)

    assert len(set(scenario.quota.reserved)) == 2


def test_the_question_is_normalised_and_searched_in_the_species_of_the_journal(
    scenario: ChatScenario,
):
    service = scenario.service(FakeChatModel(), top_k=3)

    scenario.ask(service, "  Café water?  ")

    assert scenario.uow.knowledge.searches == [("Café water?", "Basil", 3)]


def test_the_model_gets_the_sections_the_species_and_the_effort_of_the_tier(
    scenario: ChatScenario,
):
    model = FakeChatModel()

    scenario.ask(scenario.service(model), "How often to water?")

    (request,) = model.requests
    assert request.question == "How often to water?"
    assert request.species == "Basil"
    assert request.sections == scenario.sections
    assert request.effort is ModelEffort.HIGH


@pytest.mark.parametrize(
    ("tier", "effort"),
    [
        (Tier.STARTER, ModelEffort.MEDIUM),
        (Tier.HOBBYIST, ModelEffort.HIGH),
        (Tier.PREMIUM, ModelEffort.HIGH),
        (None, ModelEffort.MEDIUM),
    ],
)
def test_the_effort_follows_the_tier_and_is_medium_when_the_tier_is_unknown(
    tier: Tier | None, effort: ModelEffort
):
    scenario = ChatScenario(tier)
    model = FakeChatModel()

    reply = scenario.ask(scenario.service(model))

    assert model.requests[0].effort is effort
    assert reply.turn is not None and reply.turn.model_effort is effort


def test_only_the_last_turns_of_the_journal_are_sent_oldest_first(scenario: ChatScenario):
    for number in range(1, 6):
        scenario.uow.chat_turns.seed(scenario.journal.id, f"question {number}", f"answer {number}")
    other = scenario.uow.journals.seed(USER)
    scenario.uow.chat_turns.seed(other.id, "other journal", "other answer")
    model = FakeChatModel()

    scenario.ask(scenario.service(model, history_turns=3))

    assert [(t.question, t.answer) for t in model.requests[0].history] == [
        ("question 3", "answer 3"),
        ("question 4", "answer 4"),
        ("question 5", "answer 5"),
    ]


def test_a_zero_history_window_sends_no_turns(scenario: ChatScenario):
    scenario.uow.chat_turns.seed(scenario.journal.id)
    model = FakeChatModel()

    scenario.ask(scenario.service(model, history_turns=0))

    assert model.requests[0].history == []


def test_citations_outside_the_given_sections_are_dropped(scenario: ChatScenario):
    model = FakeChatModel(answered("S1", "S1", "S2", "S3", "S0", "X1", " s2 ", "S01"))

    reply = scenario.ask(scenario.service(model))

    assert reply.turn is not None
    assert [s.section_id for s in reply.turn.sources] == [s.id for s in scenario.sections]


def test_an_answer_without_a_valid_citation_becomes_not_in_kb_and_still_costs_a_unit(
    scenario: ChatScenario,
):
    model = FakeChatModel(answered("S9", "nonsense", text="Invented facts."))

    reply = scenario.ask(scenario.service(model))

    assert reply.turn is not None
    assert (reply.turn.outcome, reply.turn.answer) == (ChatOutcome.NOT_IN_KB, NOT_IN_KB_TEXT)
    assert reply.turn.sources == []
    assert scenario.quota.committed == scenario.quota.reserved


@pytest.mark.parametrize("outcome", [ChatOutcome.NOT_IN_KB, ChatOutcome.OUT_OF_SCOPE])
def test_refusals_are_stored_without_citations_and_spend_the_unit(
    scenario: ChatScenario, outcome: ChatOutcome
):
    model = FakeChatModel(ModelChatAnswer(outcome, "I cannot help with that.", ["S1"]))

    reply = scenario.ask(scenario.service(model), "Who won the match?")

    assert reply.turn is not None
    assert (reply.turn.outcome, reply.turn.sources) == (outcome, [])
    assert scenario.events == ["reserve", "db-commit", "commit"]


def test_an_exhausted_quota_blocks_the_request(scenario: ChatScenario):
    scenario.quota.exhausted = True
    model = FakeChatModel()

    with pytest.raises(AiQuotaExhausted):
        scenario.ask(scenario.service(model))

    assert model.requests == []
    assert scenario.uow.chat_turns.turns == {}
    assert scenario.quota.committed == scenario.quota.released == []


def test_an_unavailable_quota_service_gives_the_sections_without_an_answer(
    scenario: ChatScenario,
):
    scenario.quota.unavailable = True
    model = FakeChatModel()

    reply = scenario.ask(scenario.service(model))

    assert (reply.turn, reply.created, reply.related) == (None, False, scenario.sections)
    assert model.requests == [] and scenario.uow.chat_turns.turns == {}
    assert scenario.quota.committed == [] and scenario.quota.reserved == []


def test_an_unusable_new_reservation_is_treated_like_an_unavailable_quota(scenario: ChatScenario):
    def refuse(reservation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        raise ReservationUnusable

    scenario.quota.reserve = refuse

    reply = scenario.ask(scenario.service(FakeChatModel()))

    assert reply.turn is None and reply.related == scenario.sections


@pytest.mark.parametrize("failure", [ModelUnavailable("timeout"), InvalidModelAnswer("not json")])
def test_a_model_failure_gives_sources_only_stores_nothing_and_releases_the_unit(
    scenario: ChatScenario, failure: Exception
):
    service = scenario.service(FakeChatModel(failure))

    reply = scenario.ask(service)

    assert (reply.turn, reply.created, reply.related) == (None, False, scenario.sections)
    assert scenario.uow.chat_turns.turns == {} and scenario.uow.commits == 0
    assert scenario.quota.released == scenario.quota.reserved
    assert scenario.quota.committed == []
    assert scenario.events == ["reserve", "release"]


def test_a_failed_release_after_a_model_failure_is_tolerated(scenario: ChatScenario):
    scenario.quota.release_unavailable = True

    reply = scenario.ask(scenario.service(FakeChatModel(ModelUnavailable("down"))))

    assert reply.turn is None


def test_a_database_failure_releases_the_unit_and_propagates(scenario: ChatScenario):
    scenario.uow.fail_commit_with = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        scenario.ask(scenario.service(FakeChatModel()))

    assert scenario.quota.released == scenario.quota.reserved
    assert scenario.quota.committed == []


def test_an_unexpected_model_error_releases_the_unit_and_propagates(scenario: ChatScenario):
    with pytest.raises(ZeroDivisionError):
        scenario.ask(scenario.service(FakeChatModel(ZeroDivisionError())))

    assert scenario.quota.released == scenario.quota.reserved


@pytest.mark.parametrize("failure", [AiQuotaUnavailable(), ReservationUnusable()])
def test_a_failed_commit_leaves_the_turn_stored_and_unpaid(
    scenario: ChatScenario, failure: Exception
):
    scenario.quota.commit_fails_with = failure

    reply = scenario.ask(scenario.service(FakeChatModel()))

    assert reply.created and reply.turn is not None
    assert scenario.quota.released == []
    assert len(scenario.uow.chat_turns.turns) == 1


def test_a_retried_id_returns_the_stored_turn_without_the_model_or_the_quota(
    scenario: ChatScenario,
):
    model = FakeChatModel()
    service = scenario.service(model)
    turn_id = uuid.uuid4()
    first = scenario.ask(service, turn_id=turn_id)
    scenario.events.clear()

    again = scenario.ask(service, "Another wording?", turn_id=turn_id)

    assert again.turn == first.turn and not again.created
    assert len(model.requests) == 1 and scenario.events == []


def test_a_turn_id_of_another_journal_is_not_found(scenario: ChatScenario):
    other = scenario.uow.journals.seed(USER)
    taken = scenario.uow.chat_turns.seed(other.id)

    with pytest.raises(ChatTurnNotFound):
        scenario.ask(scenario.service(FakeChatModel()), turn_id=taken.id)

    assert scenario.quota.reserved == []


def test_a_journal_of_another_user_is_not_found_before_anything_is_reserved(
    scenario: ChatScenario,
):
    foreign = scenario.uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        scenario.service(FakeChatModel()).ask(USER, foreign.id, uuid.uuid4(), "Water?")

    assert scenario.quota.reserved == []


def test_a_duplicate_that_loses_the_race_releases_its_unit_and_returns_the_stored_turn(
    scenario: ChatScenario,
):
    turn_id = uuid.uuid4()
    model = FakeChatModel()
    winner = []
    model.during_call = lambda: winner.append(
        scenario.uow.chat_turns.seed(scenario.journal.id, id=turn_id)
    )

    reply = scenario.ask(scenario.service(model), turn_id=turn_id)

    assert reply.turn == winner[0] and not reply.created
    assert scenario.quota.released == scenario.quota.reserved
    assert scenario.quota.committed == []


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_a_blank_question_is_rejected_before_the_quota_is_touched(
    scenario: ChatScenario, text: str
):
    with pytest.raises(InvalidRequest):
        scenario.ask(scenario.service(FakeChatModel()), text)

    assert scenario.quota.reserved == [] and scenario.uow.knowledge.searches == []


@pytest.mark.parametrize("text", ["x" * (QUESTION_LIMIT + 1), "bell\x07", "nul\x00", "tab\there"])
def test_an_overlong_question_or_one_with_control_characters_is_rejected(text: str):
    with pytest.raises(InvalidRequest):
        validate_question(text)


def test_the_longest_question_and_newlines_are_accepted():
    assert validate_question("x" * QUESTION_LIMIT) == "x" * QUESTION_LIMIT
    assert validate_question("first line\nsecond line") == "first line\nsecond line"


def test_the_history_lists_the_newest_turns_first_with_a_cursor_for_the_next_page(
    scenario: ChatScenario,
):
    turns = [scenario.uow.chat_turns.seed(scenario.journal.id, f"q{n}") for n in range(5)]
    service = scenario.service(FakeChatModel())

    first = service.history(USER, scenario.journal.id, None, None)
    second = service.history(USER, scenario.journal.id, 2, first.next_cursor)
    last = service.history(USER, scenario.journal.id, 2, second.next_cursor)

    assert [t.question for t in first.items] == ["q4", "q3"]
    assert [t.question for t in second.items] == ["q2", "q1"]
    assert [t.question for t in last.items] == ["q0"] and last.next_cursor is None
    assert decode_cursor(first.next_cursor).id == turns[3].id


def test_the_history_page_size_is_capped(scenario: ChatScenario):
    for number in range(5):
        scenario.uow.chat_turns.seed(scenario.journal.id, f"q{number}")

    page = scenario.service(FakeChatModel()).history(USER, scenario.journal.id, 50, None)

    assert len(page.items) == 3 and page.next_cursor is not None


def test_the_history_of_a_foreign_journal_is_not_found(scenario: ChatScenario):
    foreign = scenario.uow.journals.seed(uuid.uuid4())

    with pytest.raises(JournalNotFound):
        scenario.service(FakeChatModel()).history(USER, foreign.id, None, None)


def test_a_malformed_cursor_is_rejected(scenario: ChatScenario):
    with pytest.raises(InvalidCursor):
        scenario.service(FakeChatModel()).history(USER, scenario.journal.id, None, "garbage")


def test_the_log_of_a_stored_turn_has_ids_and_lengths_but_no_text(
    scenario: ChatScenario, caplog: pytest.LogCaptureFixture
):
    secret = "my private question about basil"
    with caplog.at_level(logging.INFO, logger="app.service.chat"):
        scenario.ask(scenario.service(FakeChatModel(answered("S1", text="private answer"))), secret)

    (record,) = [r for r in caplog.records if r.message == "chat turn stored"]
    assert record.question_length == len(secret)
    assert record.chat_outcome == "answered"
    assert record.cited_section_ids == [str(scenario.sections[0].id)]
    assert len(record.retrieved_section_ids) == 2
    assert secret not in caplog.text and "private answer" not in caplog.text
