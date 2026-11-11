from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLANT_CARE_")

    service_name: str = "plant-care"
    log_level: str = "INFO"
    database_url: SecretStr
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    aws_connect_timeout_seconds: float = 2.0
    aws_read_timeout_seconds: float = 5.0
    aws_max_attempts: int = 3

    order_placed_queue_url: str
    consumer_wait_seconds: int = 5
    consumer_batch_size: int = 5
    consumer_interval_seconds: float = 0.5

    photos_bucket: str = "growpath-photos"
    # Presigned URLs are opened by the client, not by the service, so they may need a host
    # that differs from the one the service itself uses to reach the object store.
    photos_public_endpoint_url: str | None = None
    photo_url_ttl_seconds: int = 900

    feed_default_page_size: int = 20
    feed_max_page_size: int = 100
