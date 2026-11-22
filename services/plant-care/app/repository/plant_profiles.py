from collections.abc import Collection

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.models import PlantProfileModel
from app.service.errors import Unavailable
from app.service.knowledge_model import SyncResult
from app.service.plant_profile_model import PlantProfile

READ_COLUMNS = (
    "slug",
    "source_slug",
    "species",
    "source_name",
    "source_url",
    "license",
    "imported_on",
    "content_sha256",
    "common_name",
    "scientific_name",
    "family",
    "difficulty",
    "summary",
    "quick_answer",
    "key_takeaways",
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


class SqlAlchemyPlantProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def sync_profile(self, profile: PlantProfile) -> SyncResult:
        """Insert the profile, or replace it when the hash changed (the guarded UPDATE locks it)."""
        values = {name: getattr(profile, name) for name in READ_COLUMNS}
        if values["key_takeaways"] is not None:
            values["key_takeaways"] = list(values["key_takeaways"])
        inserted = self._session.execute(
            insert(PlantProfileModel)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[PlantProfileModel.slug])
            .returning(PlantProfileModel.slug)
        ).scalar_one_or_none()
        if inserted is not None:
            return SyncResult.CREATED
        changed = self._session.execute(
            update(PlantProfileModel)
            .where(
                PlantProfileModel.slug == profile.slug,
                PlantProfileModel.content_sha256 != profile.content_sha256,
            )
            .values(**values)
            .returning(PlantProfileModel.slug)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        return SyncResult.UNCHANGED if changed is None else SyncResult.UPDATED

    def content_hashes(self, slugs: Collection[str]) -> dict[str, str]:
        rows = self._session.execute(
            select(PlantProfileModel.slug, PlantProfileModel.content_sha256).where(
                PlantProfileModel.slug.in_(slugs)
            )
        )
        return {slug: content_sha256 for slug, content_sha256 in rows}

    def get(self, slug: str) -> PlantProfile | None:
        """Raises Unavailable when the database cannot be reached."""
        try:
            row = self._session.execute(
                select(*(getattr(PlantProfileModel, name) for name in READ_COLUMNS)).where(
                    PlantProfileModel.slug == slug
                )
            ).one_or_none()
        except (OperationalError, InterfaceError, PoolTimeout) as error:
            raise Unavailable("the database did not answer") from error
        if row is None:
            return None
        values = dict(row._mapping)
        if values["key_takeaways"] is not None:
            values["key_takeaways"] = tuple(values["key_takeaways"])
        return PlantProfile(**values)
