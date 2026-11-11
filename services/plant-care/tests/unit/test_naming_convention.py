from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import Base, DiagnosisModel, EscalationModel, JournalModel


def ddl(model: type[Base]) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_journals_carry_the_names_of_the_design():
    statement = ddl(JournalModel)

    assert "CONSTRAINT pk_journals PRIMARY KEY (id)" in statement
    assert "CONSTRAINT uq_journals_order_id UNIQUE (order_id)" in statement
    assert "CONSTRAINT ck_journals_species CHECK" in statement


def test_diagnoses_carry_the_names_of_the_design():
    statement = ddl(DiagnosisModel)

    assert "CONSTRAINT fk_diagnoses_update_id_updates FOREIGN KEY(update_id)" in statement
    assert "CONSTRAINT uq_diagnoses_ai_reservation_id UNIQUE (ai_reservation_id)" in statement
    assert "CONSTRAINT ck_diagnoses_escalation_offered CHECK" in statement


def test_escalations_name_their_partial_indexes():
    names = {index.name for index in EscalationModel.__table__.indexes}

    assert names == {
        "uq_escalations_active_update",
        "ix_escalations_update_created",
        "ix_escalations_requested",
    }
