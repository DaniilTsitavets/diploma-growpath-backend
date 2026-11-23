import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.service.errors import (
    PhotoNotUploaded,
    PhotoTooLarge,
    PhotoTypeNotAllowed,
    RootsPhotoNotRequested,
    Unavailable,
    UpdateNotFound,
)
from app.service.model import DiagnosisStatus, PhotoKind
from app.service.ports import UnitOfWork


class PhotoStorageUnavailable(Unavailable):
    def __init__(self) -> None:
        super().__init__("the photo storage did not answer")


@dataclass(frozen=True)
class StoredObject:
    size_bytes: int
    content_type: str | None


class PhotoStorage(Protocol):
    def upload_url(self, storage_key: str, content_type: str, size_bytes: int) -> str:
        """A presigned PUT URL signing type and length. Raises PhotoStorageUnavailable."""
        ...

    def download_url(self, storage_key: str) -> str:
        """A presigned GET URL for the object. Raises PhotoStorageUnavailable."""
        ...

    def head(self, storage_key: str) -> StoredObject | None:
        """The object's metadata, None when it does not exist. Raises PhotoStorageUnavailable."""
        ...

    def delete(self, storage_key: str) -> None:
        """Remove the object; a missing one is not an error. Raises PhotoStorageUnavailable."""
        ...


@dataclass(frozen=True)
class PhotoPolicy:
    allowed_content_types: tuple[str, ...]
    max_bytes: int
    url_ttl_seconds: int

    def check_declared(self, content_type: str, size_bytes: int) -> None:
        if content_type not in self.allowed_content_types:
            raise PhotoTypeNotAllowed(self.allowed_content_types)
        if not 1 <= size_bytes <= self.max_bytes:
            raise PhotoTooLarge(self.max_bytes)

    def check_stored(self, stored: StoredObject) -> None:
        self.check_declared(stored.content_type or "", stored.size_bytes)


@dataclass(frozen=True)
class PhotoUpload:
    kind: PhotoKind
    url: str
    expires_in_seconds: int
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class PhotoLink:
    kind: PhotoKind
    url: str


@dataclass(frozen=True)
class UpdatePhotos:
    photos: list[PhotoLink]


def storage_key_for(journal_id: uuid.UUID, update_id: uuid.UUID, kind: PhotoKind) -> str:
    return f"journals/{journal_id}/updates/{update_id}/{kind.value}"


class PhotoService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        storage: PhotoStorage,
        policy: PhotoPolicy,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._policy = policy

    def request_upload(
        self,
        update_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: PhotoKind,
        content_type: str,
        size_bytes: int,
    ) -> PhotoUpload:
        """Sign an upload URL bound to the declared type and size; nothing is stored yet."""
        with self._unit_of_work() as uow:
            update = uow.updates.get_owned(update_id, user_id)
            if update is None:
                raise UpdateNotFound
            self._check_kind_allowed(uow, update_id, kind)
            self._policy.check_declared(content_type, size_bytes)
            key = storage_key_for(update.journal_id, update_id, kind)
            url = self._storage.upload_url(key, content_type, size_bytes)
        return PhotoUpload(kind, url, self._policy.url_ttl_seconds, content_type, size_bytes)

    def confirm_upload(
        self, update_id: uuid.UUID, user_id: uuid.UUID, kind: PhotoKind
    ) -> PhotoLink:
        """Record the photo once the object is within the limits; otherwise delete it."""
        with self._unit_of_work() as uow:
            update = uow.updates.get_owned(update_id, user_id)
            if update is None:
                raise UpdateNotFound
            self._check_kind_allowed(uow, update_id, kind)
            key = storage_key_for(update.journal_id, update_id, kind)
            stored = self._storage.head(key)
            if stored is None:
                raise PhotoNotUploaded
            try:
                self._policy.check_stored(stored)
            except (PhotoTooLarge, PhotoTypeNotAllowed):
                self._storage.delete(key)
                uow.photos.remove(update_id, kind)
                uow.commit()
                raise
            uow.photos.add(update_id, kind, key)
            uow.commit()
        return PhotoLink(kind, self._storage.download_url(key))

    def _check_kind_allowed(self, uow: UnitOfWork, update_id: uuid.UUID, kind: PhotoKind) -> None:
        if kind is PhotoKind.ROOTS_DRAINAGE and not self._roots_photo_requested(uow, update_id):
            raise RootsPhotoNotRequested

    @staticmethod
    def _roots_photo_requested(uow: UnitOfWork, update_id: uuid.UUID) -> bool:
        diagnosis = uow.diagnoses.get(update_id)
        return (
            diagnosis is not None
            and diagnosis.summary.status is DiagnosisStatus.AWAITING_CLARIFICATION
            and diagnosis.summary.roots_photo_requested
        )

    def list_photos(self, update_id: uuid.UUID, user_id: uuid.UUID) -> UpdatePhotos:
        with self._unit_of_work() as uow:
            if uow.updates.get_owned(update_id, user_id) is None:
                raise UpdateNotFound
            photos = uow.photos.list_for_update(update_id)
        links = [
            PhotoLink(photo.kind, self._storage.download_url(photo.storage_key)) for photo in photos
        ]
        return UpdatePhotos(links)
