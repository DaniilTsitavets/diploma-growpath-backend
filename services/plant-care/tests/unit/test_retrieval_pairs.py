from pathlib import Path

from app.service.knowledge_parser import parse_document
from tests.db.retrieval_pairs import PAIRS, section_id

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"


def test_the_retrieval_pairs_point_at_sections_of_the_shipped_files():
    documents = [parse_document(path.read_text("utf-8")) for path in KNOWLEDGE_DIR.glob("*.md")]
    known = {section.id for document in documents for section in document.sections}

    unknown = [
        question for _, question, slug, anchor in PAIRS if section_id(slug, anchor) not in known
    ]

    assert unknown == []
    assert len(PAIRS) >= 12
