import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.service.errors import Unavailable, UpdateNotFound
from app.service.model import Photo, PhotoKind
from app.service.ports import UnitOfWork

# US-3.2: two photos are required; the close-up is optional and the roots photo is asked for later.
REQUIRED_KINDS = (PhotoKind.OVERVIEW, PhotoKind.SOIL)


class PhotoStorageUnavailable(Unavailable):
    def __init__(self) -> None:
        super().__init__("the photo storage could not issue a URL")


class PhotoStorage(Protocol):
    def upload_url(self, storage_key: str) -> str:
        """A presigned PUT URL for the object. Raises PhotoStorageUnavailable."""
        ...

    def download_url(self, storage_key: str) -> str:
        """A presigned GET URL for the object. Raises PhotoStorageUnavailable."""
        ...


@dataclass(frozen=True)
class PhotoUpload:
    kind: PhotoKind
    url: str
    expires_in_seconds: int


@dataclass(frozen=True)
class PhotoLink:
    kind: PhotoKind
    url: str


@dataclass(frozen=True)
class UpdatePhotos:
    photos: list[PhotoLink]
    missing_required: list[PhotoKind]


def storage_key_for(journal_id: uuid.UUID, update_id: uuid.UUID, kind: PhotoKind) -> str:
    return f"journals/{journal_id}/updates/{update_id}/{kind.value}"


def missing_required(photos: list[Photo]) -> list[PhotoKind]:
    present = {photo.kind for photo in photos}
    return [kind for kind in REQUIRED_KINDS if kind not in present]


class PhotoService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        storage: PhotoStorage,
        url_ttl_seconds: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._url_ttl_seconds = url_ttl_seconds

    def request_upload(
        self, update_id: uuid.UUID, user_id: uuid.UUID, kind: PhotoKind
    ) -> PhotoUpload:
        """Record the object key of the photo and sign an upload URL; repeating it re-signs."""
        with self._unit_of_work() as uow:
            update = uow.updates.get_owned(update_id, user_id)
            if update is None:
                raise UpdateNotFound
            key = storage_key_for(update.journal_id, update_id, kind)
            url = self._storage.upload_url(key)
            uow.photos.add(update_id, kind, key)
            uow.commit()
        return PhotoUpload(kind, url, self._url_ttl_seconds)

    def list_photos(self, update_id: uuid.UUID, user_id: uuid.UUID) -> UpdatePhotos:
        with self._unit_of_work() as uow:
            if uow.updates.get_owned(update_id, user_id) is None:
                raise UpdateNotFound
            photos = uow.photos.list_for_update(update_id)
        links = [
            PhotoLink(photo.kind, self._storage.download_url(photo.storage_key)) for photo in photos
        ]
        return UpdatePhotos(links, missing_required(photos))
