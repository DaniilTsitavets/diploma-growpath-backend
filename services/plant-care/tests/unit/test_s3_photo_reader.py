import io

import pytest
from botocore.exceptions import ClientError

from app.service.diagnosis_model import PhotoUnreadable
from app.storage.s3 import S3PhotoReader, create_s3_client, sniff_media_type
from tests.unit.settings import make_settings

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


class StubS3:
    def __init__(self, data: bytes = b"", error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.requested: list[dict] = []

    def get_object(self, **kwargs) -> dict:
        self.requested.append(kwargs)
        if self.error:
            raise self.error
        return {"ContentLength": len(self.data), "Body": io.BytesIO(self.data)}


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (b"GIF89a....", "image/gif"),
        (b"%PDF-1.7", None),
        (b"", None),
    ],
)
def test_the_media_type_is_taken_from_the_magic_bytes(data: bytes, media_type: str | None):
    assert sniff_media_type(data) == media_type


def test_a_photo_is_read_by_bucket_and_key():
    stub = StubS3(JPEG)

    image = S3PhotoReader(stub, "photos", 1000).read("journals/j/updates/u/soil")

    assert (image.media_type, image.data) == ("image/jpeg", JPEG)
    assert stub.requested == [{"Bucket": "photos", "Key": "journals/j/updates/u/soil"}]


def test_a_photo_over_the_limit_is_refused():
    with pytest.raises(PhotoUnreadable, match="large"):
        S3PhotoReader(StubS3(JPEG), "photos", len(JPEG) - 1).read("k")


def test_a_body_longer_than_its_declared_length_is_refused():
    class Lying(StubS3):
        def get_object(self, **kwargs) -> dict:
            return {"ContentLength": 1, "Body": io.BytesIO(JPEG)}

    with pytest.raises(PhotoUnreadable, match="large"):
        S3PhotoReader(Lying(), "photos", len(JPEG) - 1).read("k")


def test_an_object_that_is_not_an_image_is_refused():
    with pytest.raises(PhotoUnreadable, match="supported"):
        S3PhotoReader(StubS3(b"%PDF-1.7"), "photos", 1000).read("k")


def test_a_storage_error_is_an_unreadable_photo():
    error = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    with pytest.raises(PhotoUnreadable):
        S3PhotoReader(StubS3(error=error), "photos", 1000).read("k")


def test_the_reading_client_uses_the_internal_endpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    settings = make_settings(
        aws_endpoint_url="http://localstack:4566",
        photos_public_endpoint_url="http://127.0.0.1:4566",
    )

    internal = create_s3_client(settings, presigning=False)
    public = create_s3_client(settings)

    assert internal.meta.endpoint_url == "http://localstack:4566"
    assert public.meta.endpoint_url == "http://127.0.0.1:4566"
