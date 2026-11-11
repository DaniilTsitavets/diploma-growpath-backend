from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import AnswerModel, Base, TicketModel

DIALECT = postgresql.dialect()


def ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def test_base_metadata_applies_the_prefix_convention():
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    Table("parents", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "children",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parents.id")),
        Column("a", Integer),
        Column("b", Integer),
        UniqueConstraint("a", "b"),
        CheckConstraint("a >= 0", name="a_non_negative"),
    )

    compiled = str(CreateTable(child).compile(dialect=DIALECT))

    assert "CONSTRAINT pk_children PRIMARY KEY (id)" in compiled
    assert "CONSTRAINT fk_children_parent_id_parents FOREIGN KEY(parent_id)" in compiled
    assert "CONSTRAINT uq_children_a_b UNIQUE (a, b)" in compiled
    assert "CONSTRAINT ck_children_a_non_negative CHECK (a >= 0)" in compiled


def test_the_models_carry_the_constraint_names_of_the_design():
    tickets, answers = ddl(TicketModel), ddl(AnswerModel)

    for name in ("pk_tickets", "uq_tickets_escalation_id"):
        assert f"CONSTRAINT {name}" in tickets
    for name in ("context", "state", "times", "event_published"):
        assert f"CONSTRAINT ck_tickets_{name} CHECK" in tickets
    assert "CONSTRAINT pk_answers PRIMARY KEY (ticket_id)" in answers
    assert "CONSTRAINT fk_answers_ticket_id_tickets FOREIGN KEY" in answers
    assert "ON DELETE RESTRICT" in answers
    assert "CONSTRAINT ck_answers_body CHECK" in answers
