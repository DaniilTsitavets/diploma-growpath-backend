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

from app.models.base import Base


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

    ddl = str(CreateTable(child).compile(dialect=postgresql.dialect()))

    assert "CONSTRAINT pk_children PRIMARY KEY (id)" in ddl
    assert "CONSTRAINT fk_children_parent_id_parents FOREIGN KEY(parent_id)" in ddl
    assert "CONSTRAINT uq_children_a_b UNIQUE (a, b)" in ddl
    assert "CONSTRAINT ck_children_a_non_negative CHECK (a >= 0)" in ddl
