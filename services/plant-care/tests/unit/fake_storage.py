import uuid

from fastapi.testclient import TestClient
from httpx import Response

from app.service.photos import PhotoPolicy, PhotoStorageUnavailable, StoredObject

MAX_BYTES = 5000


def photo_policy() -> PhotoPolicy:
    return PhotoPolicy(("image/jpeg", "image/png"), MAX_BYTES, url_ttl_seconds=600)


class FakePhotoStorage:
    def __init__(self, any_object_exists: bool = False) -> None:
        self.any_object_exists = any_object_exists
        self.unavailable = False
        self.objects: dict[str, StoredObject] = {}
        self.deleted: list[str] = []

    def put(
        self, storage_key: str, size_bytes: int = 1000, content_type: str = "image/png"
    ) -> None:
        self.objects[storage_key] = StoredObject(size_bytes, content_type)

    def upload_url(self, storage_key: str, content_type: str, size_bytes: int) -> str:
        return self._url("put", storage_key)

    def download_url(self, storage_key: str) -> str:
        return self._url("get", storage_key)

    def head(self, storage_key: str) -> StoredObject | None:
        self._check_available()
        if storage_key not in self.objects and self.any_object_exists:
            return StoredObject(1000, "image/png")
        return self.objects.get(storage_key)

    def delete(self, storage_key: str) -> None:
        self._check_available()
        self.objects.pop(storage_key, None)
        self.deleted.append(storage_key)

    def _url(self, method: str, storage_key: str) -> str:
        self._check_available()
        return f"http://storage.test/{storage_key}?method={method}"

    def _check_available(self) -> None:
        if self.unavailable:
            raise PhotoStorageUnavailable


def upload_photo(
    client: TestClient,
    storage: FakePhotoStorage,
    headers: dict[str, str],
    journal_id: uuid.UUID | str,
    update_id: uuid.UUID | str,
    kind: str,
) -> Response:
    """Put an object under the expected key, then presign and confirm; returns the confirmation."""
    storage.put(f"journals/{journal_id}/updates/{update_id}/{kind}")
    return presign_and_confirm(client, headers, update_id, kind)


def presign_and_confirm(
    client: TestClient, headers: dict[str, str], update_id: uuid.UUID | str, kind: str
) -> Response:
    body = {"kind": kind, "content_type": "image/png", "size_bytes": 1000}
    client.post(f"/v1/updates/{update_id}/photos", json=body, headers=headers)
    return client.post(f"/v1/updates/{update_id}/photos/{kind}/confirm", headers=headers)
