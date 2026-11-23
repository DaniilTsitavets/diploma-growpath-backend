"""photos are optional and the soil photo kind is gone: the soil state comes from the questionnaire

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"

KINDS = "'overview', 'symptom_closeup', 'roots_drainage'"
KINDS_WITH_SOIL = "'overview', 'soil', 'symptom_closeup', 'roots_drainage'"
CONSTRAINT = op.f("ck_update_photos_kind")


def upgrade() -> None:
    op.execute("DELETE FROM update_photos WHERE kind = 'soil'")
    op.drop_constraint(CONSTRAINT, "update_photos", type_="check")
    op.create_check_constraint(CONSTRAINT, "update_photos", f"kind IN ({KINDS})")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "update_photos", type_="check")
    op.create_check_constraint(CONSTRAINT, "update_photos", f"kind IN ({KINDS_WITH_SOIL})")
