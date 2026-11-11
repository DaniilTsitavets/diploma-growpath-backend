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
