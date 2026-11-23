from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from app.service.photos import PhotoStorageUnavailable, StoredObject
from app.storage.s3 import S3PhotoStorage, create_s3_client
from tests.unit.settings import make_settings


@pytest.fixture(autouse=True)
def dummy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


def storage(**settings) -> S3PhotoStorage:
    config = make_settings(aws_endpoint_url="http://localstack:4566", **settings)
    return S3PhotoStorage(create_s3_client(config), config.photos_bucket, 600)


def test_urls_are_path_style_for_the_bucket_and_key():
    url = urlparse(storage().upload_url("journals/j/updates/u/soil", "image/png", 1234))

    assert (url.scheme, url.netloc) == ("http", "localstack:4566")
    assert url.path == "/growpath-photos/journals/j/updates/u/soil"
    assert parse_qs(url.query)["X-Amz-Expires"] == ["600"]


def test_urls_are_signed_for_the_public_endpoint_when_one_is_configured():
    url = urlparse(storage(photos_public_endpoint_url="http://127.0.0.1:4566").download_url("k"))

    assert url.netloc == "127.0.0.1:4566"


def test_upload_and_download_urls_are_signed_for_different_operations():
    photos = storage()

    assert photos.upload_url("k", "image/png", 10) != photos.download_url("k")


def test_the_upload_url_signs_the_content_type_and_length():
    url = urlparse(storage().upload_url("k", "image/png", 1234))

    signed = parse_qs(url.query)["X-Amz-SignedHeaders"][0].split(";")
    assert "content-type" in signed
    assert "content-length" in signed


def test_the_client_has_timeouts_and_the_standard_retry_mode():
    client = create_s3_client(make_settings(aws_read_timeout_seconds=7, aws_max_attempts=4))

    assert client.meta.config.read_timeout == 7
    assert client.meta.config.retries["mode"] == "standard"
    assert client.meta.config.retries["total_max_attempts"] == 5


class FailingClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def generate_presigned_url(self, *args, **kwargs):
        raise self._error

    def head_object(self, **kwargs):
        raise self._error

    def delete_object(self, **kwargs):
        raise self._error


@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")],
    ids=["no credentials", "rejected"],
)
def test_a_signing_failure_becomes_storage_unavailable(error: Exception):
    photos = S3PhotoStorage(FailingClient(error), "growpath-photos", 600)

    with pytest.raises(PhotoStorageUnavailable):
        photos.upload_url("k", "image/png", 10)


@pytest.mark.parametrize(
    "operation", [lambda photos: photos.head("k"), lambda photos: photos.delete("k")]
)
@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")],
    ids=["no credentials", "rejected"],
)
def test_a_head_or_delete_failure_becomes_storage_unavailable(operation, error: Exception):
    photos = S3PhotoStorage(FailingClient(error), "growpath-photos", 600)

    with pytest.raises(PhotoStorageUnavailable):
        operation(photos)


class StubClient:
    def __init__(self, head=None, error: Exception | None = None) -> None:
        self._head = head
        self._error = error
        self.deleted: list[dict] = []

    def head_object(self, **kwargs):
        if self._error:
            raise self._error
        return self._head

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)


def test_head_returns_the_size_and_content_type_of_the_object():
    client = StubClient({"ContentLength": 42, "ContentType": "image/png"})

    stored = S3PhotoStorage(client, "growpath-photos", 600).head("k")

    assert stored == StoredObject(42, "image/png")


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_head_returns_none_for_a_missing_object(code: str):
    client = StubClient(error=ClientError({"Error": {"Code": code}}, "HeadObject"))

    assert S3PhotoStorage(client, "growpath-photos", 600).head("k") is None


def test_delete_removes_the_object_from_the_bucket():
    client = StubClient()

    S3PhotoStorage(client, "growpath-photos", 600).delete("k")

    assert client.deleted == [{"Bucket": "growpath-photos", "Key": "k"}]
