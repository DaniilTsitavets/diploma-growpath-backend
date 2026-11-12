import pytest

from app.service.consensus import cause_key, decide, rank_candidates
from app.service.diagnosis_model import InvalidModelAnswer, ModelAnswer
from app.service.dominance import Support
from app.service.model import DiagnosisStatus
from tests.unit.fake_model import RULE, conclusion, question, ranked

H, M, L = Support.HIGH, Support.MEDIUM, Support.LOW


def answer(*causes: tuple[str, Support], recommendation: str | None = "Water less.") -> ModelAnswer:
    return ModelAnswer(ranked(*causes), recommendation, None, False)


def causes_of(answers: list[ModelAnswer]) -> list[str]:
    return [candidate.cause for candidate in rank_candidates(answers)]


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("Overwatering", "overwatering", True),
        ("  Over   watering ", "over watering", True),
        ("Over\twatering", "Over watering", True),
        ("Overwatering", "Over-watering", False),
        ("Overwatering", "Underwatering", False),
    ],
)
def test_causes_match_ignoring_case_and_whitespace(left: str, right: str, same: bool):
    assert (cause_key(left) == cause_key(right)) is same


def test_a_single_answer_keeps_the_order_of_the_model():
    only = answer(("A", M), ("B", L), ("C", H))

    assert causes_of([only]) == ["A", "B", "C"]


def test_causes_are_ranked_by_the_calls_that_put_them_first():
    first = answer(("A", H), ("B", L))
    second = answer(("B", H), ("A", L))
    third = answer(("B", M), ("C", L))

    assert causes_of([first, second, third]) == ["B", "A", "C"]


def test_equal_votes_are_ordered_by_the_best_support():
    first = answer(("A", M), ("C", L))
    second = answer(("B", H), ("C", M))

    assert causes_of([first, second]) == ["B", "A", "C"]


def test_causes_without_votes_are_ordered_by_support_then_by_arrival():
    first = answer(("A", H), ("X", L), ("Y", M), ("Z", L))
    second = answer(("A", H), ("Z", L), ("Y", M))

    assert causes_of([first, second]) == ["A", "Y", "X", "Z"]


def test_equal_votes_and_support_keep_the_order_of_arrival():
    first = answer(("A", M))
    second = answer(("B", M))

    assert causes_of([first, second]) == ["A", "B"]


def test_the_same_cause_in_another_spelling_is_merged_and_counted_once_per_call():
    first = answer(("Overwatering", H), ("overwatering", L))
    second = answer((" OVERWATERING ", M), ("Root rot", L))

    merged = rank_candidates([first, second])

    assert [candidate.cause for candidate in merged] == ["Overwatering", "Root rot"]


def test_the_merged_list_is_cut_to_the_limit():
    wide = answer(*((f"cause {n}", L) for n in range(5)))
    other = answer(*((f"other {n}", L) for n in range(5)))

    assert len(rank_candidates([wide, other])) == 5


def test_a_dominant_conclusion_has_no_question_and_no_offer():
    result = decide([conclusion(dominant=True)], 1, RULE, clarified=False)

    assert result.status is DiagnosisStatus.COMPLETED
    assert (result.has_dominant_candidate, result.clarifying_question) == (True, None)
    assert result.escalation_offered is False


def test_an_ambiguous_question_waits_for_the_user_without_a_dominant_candidate():
    result = decide([question(roots_photo=True)], 1, RULE, clarified=False)

    assert result.status is DiagnosisStatus.AWAITING_CLARIFICATION
    assert result.has_dominant_candidate is False
    assert result.clarifying_question == "Are the leaves soft?"
    assert result.roots_photo_requested is True
    assert result.recommendation is None


def test_still_ambiguous_after_the_clarification_offers_the_expert():
    result = decide([conclusion(dominant=False)], 1, RULE, clarified=True)

    assert result.status is DiagnosisStatus.COMPLETED
    assert (result.has_dominant_candidate, result.escalation_offered) == (False, True)


def test_dominant_after_the_clarification_offers_nobody():
    result = decide([conclusion(dominant=True)], 1, RULE, clarified=True)

    assert (result.has_dominant_candidate, result.escalation_offered) == (True, False)


def test_ambiguous_without_a_question_is_completed_and_offers_nobody_before_a_clarification():
    result = decide([conclusion(dominant=False)], 1, RULE, clarified=False)

    assert result.status is DiagnosisStatus.COMPLETED
    assert (result.has_dominant_candidate, result.escalation_offered) == (False, False)


def test_a_question_after_the_clarification_is_not_a_verdict():
    with pytest.raises(InvalidModelAnswer):
        decide([question()], 1, RULE, clarified=True)


def test_an_answer_with_neither_a_recommendation_nor_a_question_is_invalid():
    with pytest.raises(InvalidModelAnswer):
        decide([answer(("A", H), recommendation=None)], 1, RULE, clarified=False)


def test_no_answers_is_invalid():
    with pytest.raises(InvalidModelAnswer):
        decide([], 1, RULE, clarified=False)


def test_agreeing_dominant_calls_are_dominant():
    calls = [answer(("Overwatering", H), ("X", L)), answer(("overwatering", H), ("Y", L))]

    result = decide(calls, 2, RULE, clarified=False)

    assert result.has_dominant_candidate is True


def test_agreeing_calls_are_not_dominant_when_one_of_them_sees_a_rival():
    calls = [answer(("A", H), ("X", L)), answer(("A", H), ("X", M))]

    assert decide(calls, 2, RULE, clarified=False).has_dominant_candidate is False


def test_calls_that_disagree_on_the_first_cause_have_no_dominant_candidate():
    calls = [answer(("A", H), ("B", L)), answer(("B", H), ("A", L))]

    result = decide(calls, 2, RULE, clarified=True)

    assert result.has_dominant_candidate is False
    assert result.escalation_offered is True
    assert [c.cause for c in result.candidates] == ["A", "B"]


def test_fewer_successful_calls_than_samples_cannot_be_dominant():
    result = decide([answer(("A", H), ("B", L))], 2, RULE, clarified=False)

    assert result.has_dominant_candidate is False
    assert result.status is DiagnosisStatus.COMPLETED


def test_the_recommendation_comes_from_a_call_with_the_winning_cause():
    calls = [
        answer(("A", H), recommendation="for A"),
        answer(("B", H), recommendation="for B"),
        answer(("B", M), recommendation="for B again"),
    ]

    assert decide(calls, 3, RULE, clarified=False).recommendation == "for B"


def test_the_question_of_the_call_that_won_the_vote_is_asked():
    losing = question(text="losing question?")
    winning = ModelAnswer(ranked(("Root rot", M), ("Overwatering", L)), None, "winning?", True)
    also_winning = ModelAnswer(ranked(("root rot", M)), None, "second winning?", False)

    result = decide([losing, winning, also_winning], 3, RULE, clarified=False)

    assert result.clarifying_question == "winning?"
    assert result.roots_photo_requested is True


def test_without_a_question_among_the_winners_the_first_question_is_asked():
    asking = question(text="only question?")
    confident_a = answer(("Root rot", H))
    confident_b = answer(("root rot", H))

    result = decide([confident_a, asking, confident_b], 3, RULE, clarified=False)

    assert result.status is DiagnosisStatus.AWAITING_CLARIFICATION
    assert result.clarifying_question == "only question?"


def test_after_the_clarification_calls_that_do_not_conclude_count_as_failed():
    concluding = answer(("A", H), ("B", L))

    result = decide([concluding, question()], 2, RULE, clarified=True)

    assert result.status is DiagnosisStatus.COMPLETED
    assert result.has_dominant_candidate is False
    assert [c.cause for c in result.candidates] == ["A", "B"]
