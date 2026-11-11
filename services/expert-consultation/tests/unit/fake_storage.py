from app.service.photos import PhotoStorageUnavailable


class FakePhotoStorage:
    def __init__(self) -> None:
        self.unavailable = False
        self.issued: list[str] = []

    def download_url(self, storage_key: str) -> str:
        if self.unavailable:
            raise PhotoStorageUnavailable
        self.issued.append(storage_key)
        return f"http://storage.test/{storage_key}?method=get"
