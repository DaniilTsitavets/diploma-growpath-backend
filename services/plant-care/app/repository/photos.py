import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import UpdatePhotoModel
from app.service.model import Photo, PhotoKind


class SqlAlchemyPhotoRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, update_id: uuid.UUID, kind: PhotoKind, storage_key: str) -> bool:
        result = self._session.execute(
            insert(UpdatePhotoModel)
            .values(update_id=update_id, kind=kind.value, storage_key=storage_key)
            .on_conflict_do_nothing(
                index_elements=[UpdatePhotoModel.update_id, UpdatePhotoModel.kind]
            )
            .returning(UpdatePhotoModel.update_id)
        )
        return result.scalar_one_or_none() is not None

    def list_for_update(self, update_id: uuid.UUID) -> list[Photo]:
        rows = self._session.scalars(
            select(UpdatePhotoModel)
            .where(UpdatePhotoModel.update_id == update_id)
            .order_by(UpdatePhotoModel.created_at, UpdatePhotoModel.kind)
        )
        return [Photo(row.update_id, PhotoKind(row.kind), row.storage_key) for row in rows]
