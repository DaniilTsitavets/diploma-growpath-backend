"""create the plant profiles served by the public plant page

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"

OPTIONAL_TEXTS = (
    "common_name",
    "scientific_name",
    "family",
    "difficulty",
    "summary",
    "quick_answer",
    "light",
    "sunlight_hours",
    "watering",
    "placement",
    "soil",
    "humidity",
    "temperature",
    "pruning",
    "toxicity",
)


def upgrade() -> None:
    op.create_table(
        "plant_profiles",
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("source_slug", sa.Text(), nullable=False),
        sa.Column("species", sa.Text(), nullable=False),
        sa.Column("common_name", sa.Text(), nullable=True),
        sa.Column("scientific_name", sa.Text(), nullable=True),
        sa.Column("family", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("quick_answer", sa.Text(), nullable=True),
        sa.Column("key_takeaways", postgresql.JSONB(), nullable=True),
        sa.Column("light", sa.Text(), nullable=True),
        sa.Column("sunlight_hours", sa.Text(), nullable=True),
        sa.Column("watering", sa.Text(), nullable=True),
        sa.Column("placement", sa.Text(), nullable=True),
        sa.Column("soil", sa.Text(), nullable=True),
        sa.Column("humidity", sa.Text(), nullable=True),
        sa.Column("temperature", sa.Text(), nullable=True),
        sa.Column("pruning", sa.Text(), nullable=True),
        sa.Column("toxicity", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("imported_on", sa.Date(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_plant_profiles")),
        sa.UniqueConstraint("source_slug", name=op.f("uq_plant_profiles_source_slug")),
        sa.CheckConstraint("slug ~ '^[a-z0-9-]{1,64}$'", name=op.f("ck_plant_profiles_slug")),
        sa.CheckConstraint(
            "btrim(source_slug) <> '' AND btrim(species) <> '' AND char_length(species) <= 80"
            " AND btrim(source_name) <> '' AND char_length(source_name) <= 100",
            name=op.f("ck_plant_profiles_lengths"),
        ),
        sa.CheckConstraint(
            " AND ".join(f"char_length({name}) <= 2000" for name in OPTIONAL_TEXTS),
            name=op.f("ck_plant_profiles_text_lengths"),
        ),
        sa.CheckConstraint(
            "key_takeaways IS NULL OR (jsonb_typeof(key_takeaways) = 'array'"
            " AND jsonb_array_length(key_takeaways) <= 12)",
            name=op.f("ck_plant_profiles_key_takeaways"),
        ),
        sa.CheckConstraint(
            "source_url LIKE 'https://%' AND char_length(source_url) <= 1000",
            name=op.f("ck_plant_profiles_source_url"),
        ),
        sa.CheckConstraint(
            "btrim(license) <> '' AND char_length(license) <= 100",
            name=op.f("ck_plant_profiles_license"),
        ),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_plant_profiles_sha")),
    )


def downgrade() -> None:
    op.drop_table("plant_profiles")
