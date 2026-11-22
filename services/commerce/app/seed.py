import uuid
from dataclasses import dataclass

from growpath_observability import configure_logging
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import create_db_engine
from app.models import CatalogItem, CatalogItemLabel

SEED_NAMESPACE = uuid.UUID("7c1e2d9a-4b3f-4a6e-8d15-3f2b9c0a1e77")


@dataclass(frozen=True)
class SeedItem:
    slug: str
    name: str
    item_type: str
    plant_slug: str | None
    plant_species: str | None
    seed_count: int | None
    price_cents: int
    labels: tuple[str, ...]
    first_purchase_discount_pct: int | None = None

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(SEED_NAMESPACE, LEGACY_ID_KEYS.get(self.slug, self.slug))


def pack(
    slug: str,
    species: str,
    price_cents: int,
    seed_count: int,
    labels: tuple[str, ...] = (),
    first_purchase_discount_pct: int | None = None,
) -> SeedItem:
    return SeedItem(
        slug=slug,
        name=f"{species} Starter Pack",
        item_type="pack",
        plant_slug=slug,
        plant_species=species,
        seed_count=seed_count,
        price_cents=price_cents,
        labels=labels,
        first_purchase_discount_pct=first_purchase_discount_pct,
    )


def sensor(slug: str, name: str, price_cents: int) -> SeedItem:
    return SeedItem(slug, name, "sensor", None, None, None, price_cents, ())


BEGINNER = ("beginner",)
EASY_CARE = ("beginner", "low-maintenance")

# Species are spelled as in the plant-care knowledge base, whose plant key is the pack slug.
CATALOG = (
    pack("lavender", "Lavender", 2400, 6, ("low-maintenance",)),
    pack("holy-basil-tulsi", "Holy Basil (Tulsi)", 1800, 10, BEGINNER),
    pack("golden-pothos", "Golden Pothos", 1500, 3, EASY_CARE, first_purchase_discount_pct=20),
    pack("snake-plant", "Snake Plant", 2200, 3, EASY_CARE),
    pack("zz-plant", "ZZ Plant", 2600, 3, EASY_CARE),
    pack("jade-plant", "Jade Plant", 2000, 4, EASY_CARE),
    pack("peace-lily", "Peace Lily", 2800, 3, BEGINNER),
    pack("aloe-vera", "Aloe Vera", 1900, 3, EASY_CARE),
    pack("spider-plant", "Spider Plant", 1400, 4, BEGINNER),
    pack("monstera-deliciosa", "Monstera Deliciosa", 3600, 3, ("rare",)),
    pack("ficus-lyrata", "Fiddle Leaf Fig", 4200, 2, ("rare",)),
    pack("boston-fern", "Boston Fern", 2300, 3),
    pack("calathea-orbifolia", "Calathea Orbifolia", 3200, 3, ("rare",)),
    pack("cactus", "Cactus", 1600, 5, ("low-maintenance",)),
    pack("maranta-prayer-plant", "Maranta Prayer Plant", 2700, 3, ("rare",)),
    sensor("soil-moisture-sensor", "Soil Moisture Sensor", 1500),
    sensor("thermo-hygrometer", "Thermo-Hygrometer", 1800),
)

# The sensor was seeded before slugs existed, with an id derived from its name.
LEGACY_ID_KEYS = {"soil-moisture-sensor": "Soil Moisture Sensor"}
LEGACY_PACK_NAMES = ("Beginner's Kit", "Mint Pack", "Chili Pepper Pack", "Lavender Pack")


def seed_catalog(session: Session) -> None:
    """Insert the missing catalog items and labels and deactivate the legacy packs.

    Existing rows are left as they are: a conflict on id, name or slug means "already there".
    """
    for item in CATALOG:
        session.execute(
            insert(CatalogItem)
            .values(
                id=item.id,
                item_type=item.item_type,
                name=item.name,
                slug=item.slug,
                plant_slug=item.plant_slug,
                plant_species=item.plant_species,
                seed_count=item.seed_count,
                price_cents=item.price_cents,
                first_purchase_discount_pct=item.first_purchase_discount_pct,
            )
            .on_conflict_do_nothing()
        )
        stored_id = session.scalars(
            select(CatalogItem.id).where(CatalogItem.slug == item.slug)
        ).one()
        for label in item.labels:
            session.execute(
                insert(CatalogItemLabel)
                .values(item_id=stored_id, label=label)
                .on_conflict_do_nothing()
            )
    session.execute(
        update(CatalogItem).where(CatalogItem.name.in_(LEGACY_PACK_NAMES)).values(is_active=False)
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
