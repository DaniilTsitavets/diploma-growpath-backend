from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.repository.knowledge import SqlAlchemyKnowledgeRepository
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.knowledge_seed import KnowledgeSeeder
from tests.db.retrieval_pairs import PAIRS, section_id

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"
TOP_K = 4
MIN_RECALL = 0.8


@pytest.fixture
def repository(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], session: Session
) -> SqlAlchemyKnowledgeRepository:
    KnowledgeSeeder(unit_of_work, KNOWLEDGE_DIR).run()
    return SqlAlchemyKnowledgeRepository(session)


def test_the_expected_section_is_among_the_top_four_for_most_questions(
    repository: SqlAlchemyKnowledgeRepository,
):
    misses = []
    for species, question, slug, anchor in PAIRS:
        found = {section.id for section in repository.search(question, species, TOP_K)}
        if section_id(slug, anchor) not in found:
            misses.append(question)

    recall = 1 - len(misses) / len(PAIRS)
    assert recall >= MIN_RECALL, f"recall@{TOP_K} is {recall:.2f}; missed: {misses}"
