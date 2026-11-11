from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from app.service.photos import PhotoStorageUnavailable
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
    url = urlparse(storage().upload_url("journals/j/updates/u/soil"))

    assert (url.scheme, url.netloc) == ("http", "localstack:4566")
    assert url.path == "/growpath-photos/journals/j/updates/u/soil"
    assert parse_qs(url.query)["X-Amz-Expires"] == ["600"]


def test_urls_are_signed_for_the_public_endpoint_when_one_is_configured():
    url = urlparse(storage(photos_public_endpoint_url="http://127.0.0.1:4566").download_url("k"))

    assert url.netloc == "127.0.0.1:4566"


def test_upload_and_download_urls_are_signed_for_different_operations():
    photos = storage()

    assert photos.upload_url("k") != photos.download_url("k")


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


@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")],
    ids=["no credentials", "rejected"],
)
def test_a_signing_failure_becomes_storage_unavailable(error: Exception):
    photos = S3PhotoStorage(FailingClient(error), "growpath-photos", 600)

    with pytest.raises(PhotoStorageUnavailable):
        photos.upload_url("k")
