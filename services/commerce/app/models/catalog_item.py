import uuid

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CatalogItem(Base):
    """A pack or the separately sold sensor. Items are deactivated, never deleted."""

    __tablename__ = "catalog_items"
    __table_args__ = (
        CheckConstraint("item_type IN ('pack', 'sensor')", name="item_type"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_length"),
        CheckConstraint(
            "char_length(plant_species) BETWEEN 1 AND 80 AND btrim(plant_species) <> ''",
            name="plant_species",
        ),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("first_purchase_discount_pct BETWEEN 1 AND 99", name="discount_pct"),
        CheckConstraint(
            "(item_type = 'pack' AND plant_species IS NOT NULL AND seed_count IS NOT NULL)"
            " OR (item_type = 'sensor' AND plant_species IS NULL AND seed_count IS NULL)",
            name="kind",
        ),
        CheckConstraint("seed_count > 0", name="seed_count_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    item_type: Mapped[str]
    name: Mapped[str] = mapped_column(unique=True)
    plant_species: Mapped[str | None]
    seed_count: Mapped[int | None]
    price_cents: Mapped[int]
    first_purchase_discount_pct: Mapped[int | None] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    labels: Mapped[list["CatalogItemLabel"]] = relationship(lazy="selectin")


class CatalogItemLabel(Base):
    __tablename__ = "catalog_item_labels"
    __table_args__ = (CheckConstraint("char_length(label) BETWEEN 1 AND 40", name="label_length"),)

    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)
    label: Mapped[str] = mapped_column(primary_key=True)
