from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import EndpointConnectionError

from app.service.photos import PhotoStorageUnavailable
from app.storage.s3 import S3PhotoStorage, create_s3_client
from tests.unit.settings import make_settings

BUCKET = "growpath-photos"


@pytest.fixture(autouse=True)
def dummy_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


def test_a_presigned_get_url_names_the_bucket_key_and_lifetime():
    client = create_s3_client(make_settings(aws_endpoint_url="http://localstack:4566"))

    url = S3PhotoStorage(client, BUCKET, 900).download_url("journals/j/updates/u/overview")

    parsed = urlparse(url)
    assert parsed.netloc == "localstack:4566"
    assert parsed.path == f"/{BUCKET}/journals/j/updates/u/overview"
    assert parse_qs(parsed.query)["X-Amz-Expires"] == ["900"]


def test_the_url_is_signed_for_the_public_endpoint_when_there_is_one():
    client = create_s3_client(
        make_settings(
            aws_endpoint_url="http://localstack:4566",
            photos_public_endpoint_url="http://127.0.0.1:4566",
        )
    )

    url = S3PhotoStorage(client, BUCKET, 900).download_url("k")

    assert urlparse(url).netloc == "127.0.0.1:4566"


def test_a_signing_failure_becomes_storage_unavailable():
    class FailingClient:
        def generate_presigned_url(self, *args, **kwargs):
            raise EndpointConnectionError(endpoint_url="http://s3")

    with pytest.raises(PhotoStorageUnavailable):
        S3PhotoStorage(FailingClient(), BUCKET, 900).download_url("k")


def test_the_client_uses_path_addressing_and_the_configured_timeouts():
    client = create_s3_client(make_settings(aws_read_timeout_seconds=9))

    assert client.meta.config.read_timeout == 9
    assert client.meta.config.s3["addressing_style"] == "path"
