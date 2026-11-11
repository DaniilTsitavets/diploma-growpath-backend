import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.service.photos import PhotoStorageUnavailable


def create_s3_client(settings: Settings):
    # Presigned URLs are opened by the expert's browser, so they are signed for the public
    # endpoint; the service itself never reads the objects.
    endpoint = settings.photos_public_endpoint_url or settings.aws_endpoint_url
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


class S3PhotoStorage:
    def __init__(self, client, bucket: str, url_ttl_seconds: int) -> None:
        self._client = client
        self._bucket = bucket
        self._url_ttl_seconds = url_ttl_seconds

    def download_url(self, storage_key: str) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": storage_key},
                ExpiresIn=self._url_ttl_seconds,
            )
        except (BotoCoreError, ClientError) as error:
            raise PhotoStorageUnavailable from error
