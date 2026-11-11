from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMMERCE_")

    service_name: str = "commerce"
    log_level: str = "INFO"
    database_url: SecretStr
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    order_placed_topic_arn: str
    republish_interval_seconds: float = 10.0
    republish_grace_seconds: float = 30.0
    republish_batch_size: int = 50
