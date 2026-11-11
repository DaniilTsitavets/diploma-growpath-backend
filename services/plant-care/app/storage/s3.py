import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.service.diagnosis_model import Image, PhotoUnreadable
from app.service.photos import PhotoStorageUnavailable


def create_s3_client(settings: Settings, presigning: bool = True):
    # Presigned URLs are opened by the client of the API, so they are signed for the public
    # endpoint; the service itself reads objects through the internal one.
    public = settings.photos_public_endpoint_url if presigning else None
    endpoint = public or settings.aws_endpoint_url
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=endpoint,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=settings.aws_connect_timeout_seconds,
            read_timeout=settings.aws_read_timeout_seconds,
            retries={"max_attempts": settings.aws_max_attempts, "mode": "standard"},
        ),
    )


def sniff_media_type(data: bytes) -> str | None:
    """The type by magic bytes: a presigned PUT does not force the Content-Type of the object."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    return None


class S3PhotoReader:
    """Reads photo bytes for the vision model, within a size limit."""

    def __init__(self, client, bucket: str, max_bytes: int) -> None:
        self._client = client
        self._bucket = bucket
        self._max_bytes = max_bytes

    def read(self, storage_key: str) -> Image:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            if response.get("ContentLength", 0) > self._max_bytes:
                raise PhotoUnreadable("the photo is too large")
            data = response["Body"].read(self._max_bytes + 1)
        except (BotoCoreError, ClientError) as error:
            raise PhotoUnreadable("the photo could not be read") from error
        if len(data) > self._max_bytes:
            raise PhotoUnreadable("the photo is too large")
        media_type = sniff_media_type(data)
        if media_type is None:
            raise PhotoUnreadable("the object is not a supported image")
        return Image(media_type, data)


class S3PhotoStorage:
    def __init__(self, client, bucket: str, url_ttl_seconds: int) -> None:
        self._client = client
        self._bucket = bucket
        self._url_ttl_seconds = url_ttl_seconds

    def upload_url(self, storage_key: str) -> str:
        return self._presign("put_object", storage_key)

    def download_url(self, storage_key: str) -> str:
        return self._presign("get_object", storage_key)

    def _presign(self, operation: str, storage_key: str) -> str:
        try:
            return self._client.generate_presigned_url(
                operation,
                Params={"Bucket": self._bucket, "Key": storage_key},
                ExpiresIn=self._url_ttl_seconds,
            )
        except (BotoCoreError, ClientError) as error:
            raise PhotoStorageUnavailable from error
