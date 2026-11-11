from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXPERT_CONSULTATION_")

    service_name: str = "expert-consultation"
    log_level: str = "INFO"
    database_url: SecretStr
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    aws_connect_timeout_seconds: float = 2.0
    aws_read_timeout_seconds: float = 5.0
    aws_max_attempts: int = 3

    escalation_queue_url: str
    consumer_wait_seconds: int = 5
    consumer_batch_size: int = Field(default=5, ge=1, le=10)
    consumer_interval_seconds: float = Field(default=0.5, gt=0)

    ticket_events_topic_arn: str
    # The author's assumption (72 hours), not an SLA from the BA; counted from ticket creation.
    ticket_ttl_seconds: int = Field(default=259200, gt=0)
    # The sweep and the terminal-event republisher share one in-process loop.
    maintenance_interval_seconds: float = Field(default=10.0, gt=0)
    # A terminal event whose record is still missing after this long is sent again.
    republish_grace_seconds: float = Field(default=30.0, ge=0)
    maintenance_batch_size: int = Field(default=50, ge=1)

    photos_bucket: str = "growpath-photos"
    # Presigned URLs are opened by the expert's browser, so they may need a host that differs
    # from the one the service itself uses to reach the object store.
    photos_public_endpoint_url: str | None = None
    photo_url_ttl_seconds: int = Field(default=900, gt=0)

    queue_default_page_size: int = Field(default=20, ge=1)
    queue_max_page_size: int = Field(default=100, ge=1)
    unavailable_retry_after_seconds: int = 5
