from app.service.photos import PhotoStorageUnavailable


class FakePhotoStorage:
    def __init__(self) -> None:
        self.unavailable = False

    def upload_url(self, storage_key: str) -> str:
        return self._url("put", storage_key)

    def download_url(self, storage_key: str) -> str:
        return self._url("get", storage_key)

    def _url(self, method: str, storage_key: str) -> str:
        if self.unavailable:
            raise PhotoStorageUnavailable
        return f"http://storage.test/{storage_key}?method={method}"
