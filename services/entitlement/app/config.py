from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ENTITLEMENT_")

    service_name: str = "entitlement"
    log_level: str = "INFO"
    database_url: SecretStr
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    order_placed_queue_url: str
    ticket_events_queue_url: str
    consumer_wait_seconds: int = 5
    consumer_batch_size: int = 5
    consumer_interval_seconds: float = 0.5

    hobbyist_threshold_cents: int = 4000
    premium_threshold_cents: int = 9000

    # The ceilings are not in the BA; these defaults are assumptions the author has to confirm.
    assumed_ai_limit_per_day_starter: int = 3
    assumed_ai_limit_per_day_hobbyist: int = 10
    assumed_ai_limit_per_day_premium: int = 30
    assumed_expert_limit_per_30_days_starter: int = 0
    assumed_expert_limit_per_30_days_hobbyist: int = 2
    assumed_expert_limit_per_30_days_premium: int = 5

    ai_reservation_ttl_minutes: int = 5
    expert_reservation_ttl_hours: int = 24

    # Kept below the 5 s client timeout of the callers (docs/db/entitlement.md, Reserve).
    lock_timeout_seconds: float = 2.0
    lock_retry_after_seconds: int = 1
