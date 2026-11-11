import uuid
from dataclasses import dataclass

from growpath_observability import configure_logging
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import create_db_engine
from app.models import CatalogItem, CatalogItemLabel

SEED_NAMESPACE = uuid.UUID("7c1e2d9a-4b3f-4a6e-8d15-3f2b9c0a1e77")


@dataclass(frozen=True)
class SeedItem:
    name: str
    item_type: str
    plant_species: str | None
    seed_count: int | None
    price_cents: int
    labels: tuple[str, ...]
    first_purchase_discount_pct: int | None = None

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(SEED_NAMESPACE, self.name)


# Demo catalog. Prices are chosen so that a few orders cross the $40 and $90 tier thresholds.
CATALOG = (
    SeedItem("Beginner's Kit", "pack", "Basil", 5, 2500, ("beginner",), 20),
    SeedItem("Mint Pack", "pack", "Mint", 10, 1800, ("beginner", "low-maintenance")),
    SeedItem("Chili Pepper Pack", "pack", "Chili pepper", 8, 3200, ("rare",)),
    SeedItem("Lavender Pack", "pack", "Lavender", 6, 4500, ("rare", "low-maintenance")),
    SeedItem("Soil Moisture Sensor", "sensor", None, None, 1500, ()),
)


def seed_catalog(session: Session) -> None:
    """Insert the demo catalog; rows that already exist are left untouched."""
    for item in CATALOG:
        session.execute(
            insert(CatalogItem)
            .values(
                id=item.id,
                item_type=item.item_type,
                name=item.name,
                plant_species=item.plant_species,
                seed_count=item.seed_count,
                price_cents=item.price_cents,
                first_purchase_discount_pct=item.first_purchase_discount_pct,
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        for label in item.labels:
            session.execute(
                insert(CatalogItemLabel)
                .values(item_id=item.id, label=label)
                .on_conflict_do_nothing()
            )


def main() -> None:
    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    engine = create_db_engine(settings)
    with Session(engine) as session, session.begin():
        seed_catalog(session)
    engine.dispose()


if __name__ == "__main__":
    main()
