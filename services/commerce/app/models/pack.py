import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Pack(Base):
    """A catalog item; `species` is empty for non-plant items such as a sensor."""

    __tablename__ = "packs"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("seed_count >= 0", name="seed_count_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    species: Mapped[str | None] = mapped_column(String(80))
    seed_count: Mapped[int]
    price_cents: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    labels: Mapped[list["PackLabel"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class PackLabel(Base):
    __tablename__ = "pack_labels"

    pack_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("packs.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(40), primary_key=True)
