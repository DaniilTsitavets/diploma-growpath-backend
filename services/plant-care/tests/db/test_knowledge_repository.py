from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.models import KbDocumentModel, KbSectionModel
from app.repository.knowledge import SqlAlchemyKnowledgeRepository
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.knowledge_model import SyncResult
from app.service.knowledge_seed import KnowledgeSeeder
from tests.db.knowledge_data import make_document

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"


def repository(session: Session) -> SqlAlchemyKnowledgeRepository:
    return SqlAlchemyKnowledgeRepository(session)


def headings(sections) -> list[str]:
    return [section.heading for section in sections]


def seed_documents(session: Session, *documents) -> SqlAlchemyKnowledgeRepository:
    repo = repository(session)
    for document in documents:
        repo.sync(document)
    return repo


def section_rows(engine: Engine) -> list[tuple]:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT id, xmin::text, heading, body FROM kb_sections ORDER BY id")
        ).all()


def test_a_new_document_is_stored_with_its_sections_and_a_search_vector(session: Session):
    document = make_document(sections={"Watering": "Water often.", "Light": "Sun."})

    result = repository(session).sync(document)

    stored = session.execute(
        select(KbSectionModel.id, KbSectionModel.position, KbSectionModel.search_vector).order_by(
            KbSectionModel.position
        )
    ).all()
    assert result is SyncResult.CREATED
    assert [row.id for row in stored] == [section.id for section in document.sections]
    assert all(row.search_vector for row in stored)
    assert session.get(KbDocumentModel, document.id).content_sha256 == document.content_sha256


def test_syncing_the_same_content_again_changes_nothing(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    seeder = KnowledgeSeeder(unit_of_work, KNOWLEDGE_DIR)
    first = seeder.run()
    before = section_rows(engine)

    second = seeder.run()

    assert set(first.values()) == {SyncResult.CREATED}
    assert set(second.values()) == {SyncResult.UNCHANGED}
    assert section_rows(engine) == before


def test_a_changed_file_replaces_the_sections_and_keeps_the_document(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    first = make_document(sections={"Watering": "Water often.", "Light": "Sun."})
    changed = make_document(
        title="New title", sections={"Watering": "Water rarely.", "Feeding": "Feed monthly."}
    )
    with unit_of_work() as uow:
        uow.knowledge.sync(first)
        uow.commit()
    with engine.connect() as connection:
        created_at = connection.scalar(select(KbDocumentModel.created_at))

    with unit_of_work() as uow:
        result = uow.knowledge.sync(changed)
        uow.commit()

    with engine.connect() as connection:
        document = connection.execute(select(KbDocumentModel)).one()
        sections = connection.execute(
            select(KbSectionModel.anchor, KbSectionModel.body).order_by(KbSectionModel.position)
        ).all()
    assert result is SyncResult.UPDATED
    assert document.title == "New title"
    assert document.content_sha256 == changed.content_sha256
    assert document.created_at == created_at
    assert sections == [("watering", "Water rarely."), ("feeding", "Feed monthly.")]


def test_the_shipped_knowledge_base_loads_every_document(
    unit_of_work: Callable[[], SqlAlchemyUnitOfWork], engine: Engine
):
    KnowledgeSeeder(unit_of_work, KNOWLEDGE_DIR).run()

    with engine.connect() as connection:
        species = dict(
            connection.execute(select(KbDocumentModel.slug, KbDocumentModel.species)).all()
        )
        sections = connection.scalar(select(func.count()).select_from(KbSectionModel))
    assert species == {
        "basil": "Basil",
        "mint": "Mint",
        "chili-pepper": "Chili pepper",
        "lavender": "Lavender",
        "general-care": None,
    }
    assert sections >= 40


def test_only_the_journal_species_and_general_sections_are_searched(session: Session):
    repo = seed_documents(
        session,
        make_document("basil", "Basil", {"Basil watering": "Water basil gently."}),
        make_document("mint", "Mint", {"Mint watering": "Water mint often."}),
        make_document("general", None, {"General watering": "Water when the soil is dry."}),
    )

    found = repo.search("watering", "Basil", 4)

    assert set(headings(found)) == {"Basil watering", "General watering"}


def test_the_species_is_matched_without_regard_to_case(session: Session):
    repo = seed_documents(session, make_document("chili", "Chili pepper", {"Heat": "Hot fruit."}))

    assert headings(repo.search("hot fruit", "CHILI PEPPER", 4)) == ["Heat"]


def test_on_equal_rank_the_species_section_comes_before_the_general_one(session: Session):
    same = {"Watering": "Water when the soil is dry."}
    repo = seed_documents(
        session,
        make_document("a-general", None, same, title="General"),
        make_document("z-basil", "Basil", same, title="Basil guide"),
    )

    found = repo.search("watering soil", "Basil", 4)

    assert [section.document_title for section in found] == ["Basil guide", "General"]


def test_a_heading_match_ranks_above_a_body_match(session: Session):
    repo = seed_documents(
        session,
        make_document(
            "basil",
            "Basil",
            {
                "Soil": "Use a light mix with some compost.",
                "Pinching": "Remove the tips of the stems regularly.",
                "Harvest": "Pinching the tips gives more leaves.",
            },
        ),
    )

    assert headings(repo.search("pinching", "Basil", 4))[:2] == ["Pinching", "Harvest"]


def test_the_stemmer_matches_inflected_words(session: Session):
    repo = seed_documents(
        session, make_document("basil", "Basil", {"Water": "Water the plants every week."})
    )

    assert headings(repo.search("How often are you watering plants", "Basil", 1)) == ["Water"]


def test_matches_are_limited_to_the_requested_number(session: Session):
    sections = {f"Section {number}": f"basil {'water ' * number}" for number in range(1, 7)}
    repo = seed_documents(session, make_document("basil", "Basil", sections))

    assert len(repo.search("basil water", "Basil", 3)) == 3


def test_fewer_matches_than_the_limit_are_topped_up_with_the_species_sections_in_order(
    session: Session,
):
    repo = seed_documents(
        session,
        make_document(
            "basil",
            "Basil",
            {"One": "alpha", "Two": "beta", "Three": "gamma needle", "Four": "delta", "Five": "e"},
        ),
        make_document("general", None, {"Unrelated": "nothing in common", "Other": "still none"}),
    )

    found = repo.search("needle", "Basil", 4)

    assert headings(found) == ["Three", "One", "Two", "Four"]


def test_the_top_up_never_takes_general_sections(session: Session):
    repo = seed_documents(
        session,
        make_document("basil", "Basil", {"Only": "needle"}),
        make_document("general", None, {"Unrelated": "nothing", "Other": "none"}),
    )

    assert headings(repo.search("needle", "Basil", 4)) == ["Only"]


def test_a_question_of_stop_words_gets_the_species_sections_by_position(session: Session):
    repo = seed_documents(
        session,
        make_document("basil", "Basil", {"First": "alpha", "Second": "beta", "Third": "gamma"}),
        make_document("general", None, {"Unrelated": "alpha"}),
    )

    assert headings(repo.search("what is the of and", "Basil", 2)) == ["First", "Second"]


def test_a_species_without_a_document_finds_only_general_matches(session: Session):
    repo = seed_documents(
        session,
        make_document("basil", "Basil", {"Basil only": "needle"}),
        make_document("general", None, {"General": "needle"}),
    )

    assert headings(repo.search("needle", "Orchid", 4)) == ["General"]
    assert repo.search("nothing matches this zzz", "Orchid", 4) == []


def test_a_found_section_carries_the_document_title_and_source(session: Session):
    repo = seed_documents(
        session, make_document("basil", "Basil", {"Watering": "Water."}, title="Basil guide")
    )

    (section,) = repo.search("water", "Basil", 4)

    assert section.document_title == "Basil guide"
    assert section.source_url == "https://example.test/basil"
    assert section.body == "Water."


HOSTILE_QUESTIONS = [
    "",
    "   ",
    "'",
    "''''",
    "\\",
    "it's a plant's \\ problem",
    "& | ! : * ( ) < >",
    "a&b|c!d:e*f(g)h<->i<2>j",
    "'; DROP TABLE kb_sections; --",
    "http://example.test/a?b=(c)&d=e:f",
    "<kb_section id='S1'>x</kb_section> <question>",
    "basil:* & !water | (soil <-> mint)",
    "x" * 1000,
    " ".join(f"word{number}" for number in range(150)),
    "ünïcödé 水 やる 🌱",
]


@pytest.mark.parametrize("question", HOSTILE_QUESTIONS)
def test_hostile_questions_never_break_the_search(session: Session, question: str):
    repo = seed_documents(
        session, make_document("basil", "Basil", {"Watering": "Water the basil.", "Light": "Sun."})
    )

    found = repo.search(question, "Basil", 4)

    assert len(found) <= 4
    assert session.scalar(select(func.count()).select_from(KbSectionModel)) == 2


def test_the_species_of_the_journal_is_bound_not_interpolated(session: Session):
    repo = seed_documents(session, make_document("basil", "Basil", {"Watering": "Water."}))

    assert repo.search("water", "x' OR '1'='1", 4) == []
