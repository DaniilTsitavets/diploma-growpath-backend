from collections.abc import Sequence
from dataclasses import dataclass

from app.service.diagnosis_answer import MAX_CANDIDATES
from app.service.diagnosis_model import InvalidModelAnswer, ModelAnswer
from app.service.dominance import DominanceRule, Support, is_dominant
from app.service.model import Candidate, DiagnosisResult, DiagnosisStatus


def cause_key(cause: str) -> str:
    """Causes are the same when they differ only in letter case and whitespace."""
    return " ".join(cause.casefold().split())


@dataclass
class _Tally:
    key: str
    candidate: Candidate
    votes: int
    support: Support
    order: int


def _tally(answers: Sequence[ModelAnswer]) -> list[_Tally]:
    """Causes ranked by the calls that put them first, then by best support, then by arrival."""
    tallies: dict[str, _Tally] = {}
    for answer in answers:
        for position, ranked in enumerate(answer.candidates):
            key = cause_key(ranked.cause)
            vote = 1 if position == 0 else 0
            tally = tallies.get(key)
            if tally is None:
                candidate = Candidate(ranked.cause, ranked.rationale)
                tallies[key] = _Tally(key, candidate, vote, ranked.support, len(tallies))
                continue
            tally.votes += vote
            tally.support = max(tally.support, ranked.support, key=lambda s: s.level)
    return sorted(tallies.values(), key=lambda t: (-t.votes, -t.support.level, t.order))


def rank_candidates(answers: Sequence[ModelAnswer]) -> list[Candidate]:
    """A single answer keeps the order of the model; several are merged by votes."""
    if len(answers) == 1:
        return [Candidate(item.cause, item.rationale) for item in answers[0].candidates]
    return [tally.candidate for tally in _tally(answers)][:MAX_CANDIDATES]


def decide(
    answers: Sequence[ModelAnswer], samples: int, rule: DominanceRule, clarified: bool
) -> DiagnosisResult:
    """Turn the answers of the successful model calls into the result of one attempt.

    Dominance needs all `samples` calls to succeed, to agree on the first cause and to be
    dominant each. A question is possible only in the first round, a verdict is due after it.
    """
    usable = [a for a in answers if not clarified or a.recommendation is not None]
    if not usable:
        raise InvalidModelAnswer("the model did not conclude after the clarification")
    candidates = rank_candidates(usable)
    winner = cause_key(candidates[0].cause)
    leaders = [a for a in usable if cause_key(a.candidates[0].cause) == winner]
    dominant = (
        len(usable) == samples
        and len(leaders) == samples
        and all(is_dominant([c.support for c in a.candidates], rule) for a in usable)
    )
    asker = None if clarified else _asker(leaders, usable)
    if asker is not None:
        return DiagnosisResult(
            status=DiagnosisStatus.AWAITING_CLARIFICATION,
            has_dominant_candidate=dominant,
            candidates=candidates,
            recommendation=None,
            clarifying_question=asker.clarifying_question,
            roots_photo_requested=asker.request_roots_photo,
            escalation_offered=False,
        )
    recommendation = next((a.recommendation for a in (*leaders, *usable) if a.recommendation), None)
    if recommendation is None:
        raise InvalidModelAnswer("the model gave neither a recommendation nor a question")
    return DiagnosisResult(
        status=DiagnosisStatus.COMPLETED,
        has_dominant_candidate=dominant,
        candidates=candidates,
        recommendation=recommendation,
        clarifying_question=None,
        roots_photo_requested=False,
        escalation_offered=clarified and not dominant,
    )


def _asker(leaders: Sequence[ModelAnswer], usable: Sequence[ModelAnswer]) -> ModelAnswer | None:
    """The question of a call that agrees with the winning cause, else the first question."""
    return next(
        (a for a in (*leaders, *usable) if a.clarifying_question is not None),
        None,
    )
