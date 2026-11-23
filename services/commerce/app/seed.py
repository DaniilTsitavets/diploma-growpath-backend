import uuid
from dataclasses import dataclass

from growpath_observability import configure_logging
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.catalog_labels import LabelTable, load_label_table
from app.config import Settings
from app.db import create_db_engine
from app.models import CatalogItem, CatalogItemLabel

SEED_NAMESPACE = uuid.UUID("7c1e2d9a-4b3f-4a6e-8d15-3f2b9c0a1e77")
MAX_LABELS_PER_ITEM = 10


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

    def wanted_labels(self, derived: LabelTable) -> frozenset[str]:
        """The hand-set labels plus the ones derived from the plant's profile."""
        wanted = frozenset(self.labels) | frozenset(derived.get(self.plant_slug or "", ()))
        if len(wanted) > MAX_LABELS_PER_ITEM:
            raise ValueError(f"{self.slug}: more than {MAX_LABELS_PER_ITEM} labels")
        return wanted

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


# Only the labels PlantSolve cannot tell are set here; the rest come from catalog_labels.json.
RARE = ("rare",)

# Species are spelled as in the plant-care knowledge base, whose plant key is the pack slug.
CATALOG = (
    pack("lavender", "Lavender", 2400, 6),
    pack("holy-basil-tulsi", "Holy Basil (Tulsi)", 1800, 10),
    pack("golden-pothos", "Golden Pothos", 1500, 3, first_purchase_discount_pct=20),
    pack("snake-plant", "Snake Plant", 2200, 3),
    pack("zz-plant", "ZZ Plant", 2600, 3),
    pack("jade-plant", "Jade Plant", 2000, 4),
    pack("peace-lily", "Peace Lily", 2800, 3),
    pack("aloe-vera", "Aloe Vera", 1900, 3),
    pack("spider-plant", "Spider Plant", 1400, 4),
    pack("monstera-deliciosa", "Monstera Deliciosa", 3600, 3, RARE),
    pack("ficus-lyrata", "Fiddle Leaf Fig", 4200, 2, RARE),
    pack("boston-fern", "Boston Fern", 2300, 3),
    pack("calathea-orbifolia", "Calathea Orbifolia", 3200, 3, RARE),
    pack("cactus", "Cactus", 1600, 5),
    pack("maranta-prayer-plant", "Maranta Prayer Plant", 2700, 3, RARE),
    sensor("soil-moisture-sensor", "Soil Moisture Sensor", 1500),
    sensor("thermo-hygrometer", "Thermo-Hygrometer", 1800),
)

# The sensor was seeded before slugs existed, with an id derived from its name.
LEGACY_ID_KEYS = {"soil-moisture-sensor": "Soil Moisture Sensor"}
LEGACY_PACK_NAMES = ("Beginner's Kit", "Mint Pack", "Chili Pepper Pack", "Lavender Pack")


def seed_catalog(session: Session, derived: LabelTable | None = None) -> None:
    """Insert the missing catalog items, deactivate the legacy packs and sync the item labels.

    Existing items are left as they are: a conflict on id, name or slug means "already there".
    The labels of a catalog item are owned by the seed: those that are neither hand-set nor in
    the derived table (`derived` defaults to catalog_labels.json) are removed.
    """
    derived = load_label_table() if derived is None else derived
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
        sync_labels(session, stored_id, item.wanted_labels(derived))
    session.execute(
        update(CatalogItem).where(CatalogItem.name.in_(LEGACY_PACK_NAMES)).values(is_active=False)
    )


def sync_labels(session: Session, item_id: uuid.UUID, wanted: frozenset[str]) -> None:
    session.execute(
        delete(CatalogItemLabel).where(
            CatalogItemLabel.item_id == item_id, CatalogItemLabel.label.not_in(wanted)
        )
    )
    for label in sorted(wanted):
        session.execute(
            insert(CatalogItemLabel).values(item_id=item_id, label=label).on_conflict_do_nothing()
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
