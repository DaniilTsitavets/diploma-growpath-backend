from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

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
TEXT_LENGTHS = " AND ".join(f"char_length({name}) <= 2000" for name in OPTIONAL_TEXTS)


class PlantProfileModel(Base):
    """The facts of one plant page, imported from the record of its knowledge document."""

    __tablename__ = "plant_profiles"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9-]{1,64}$'", name="slug"),
        CheckConstraint(
            "btrim(source_slug) <> '' AND btrim(species) <> '' AND char_length(species) <= 80"
            " AND btrim(source_name) <> '' AND char_length(source_name) <= 100",
            name="lengths",
        ),
        CheckConstraint(TEXT_LENGTHS, name="text_lengths"),
        CheckConstraint(
            "key_takeaways IS NULL OR (jsonb_typeof(key_takeaways) = 'array'"
            " AND jsonb_array_length(key_takeaways) <= 12)",
            name="key_takeaways",
        ),
        CheckConstraint(
            "source_url LIKE 'https://%' AND char_length(source_url) <= 1000", name="source_url"
        ),
        CheckConstraint("btrim(license) <> '' AND char_length(license) <= 100", name="license"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="sha"),
    )

    slug: Mapped[str] = mapped_column(primary_key=True)
    source_slug: Mapped[str] = mapped_column(unique=True)
    species: Mapped[str]
    common_name: Mapped[str | None]
    scientific_name: Mapped[str | None]
    family: Mapped[str | None]
    difficulty: Mapped[str | None]
    summary: Mapped[str | None]
    quick_answer: Mapped[str | None]
    # Without none_as_null, None becomes the JSON value `null`, which the check rejects.
    key_takeaways: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    light: Mapped[str | None]
    sunlight_hours: Mapped[str | None]
    watering: Mapped[str | None]
    placement: Mapped[str | None]
    soil: Mapped[str | None]
    humidity: Mapped[str | None]
    temperature: Mapped[str | None]
    pruning: Mapped[str | None]
    toxicity: Mapped[str | None]
    source_name: Mapped[str]
    source_url: Mapped[str]
    license: Mapped[str]
    imported_on: Mapped[date]
    content_sha256: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
