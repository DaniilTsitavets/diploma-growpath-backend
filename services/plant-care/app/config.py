from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLANT_CARE_", protected_namespaces=("settings_",))

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

    unavailable_retry_after_seconds: int = 5
    entitlement_url: str
    entitlement_connect_timeout_seconds: float = 1.0
    entitlement_read_timeout_seconds: float = 5.0
    entitlement_max_connections: int = 20
    entitlement_max_attempts: int = 2
    entitlement_retry_base_delay_seconds: float = 0.1
    entitlement_retry_max_delay_seconds: float = 1.0
    entitlement_breaker_failure_threshold: int = 5
    entitlement_breaker_reset_seconds: float = 10.0

    escalation_topic_arn: str
    # Must equal the expert reservation TTL of the entitlement service: the repeater releases the
    # reservation when this much time has passed since the escalation.
    expert_reservation_ttl_seconds: int = 86400
    # Far above any clock skew between the plant-care and expert-consultation databases.
    escalation_ack_margin_seconds: int = 900
    escalation_history_limit: int = 10
    escalation_repeater_interval_seconds: float = 10.0
    escalation_repeater_grace_seconds: float = 30.0
    escalation_repeater_batch_size: int = 50

    # `mock` needs no key; its behaviour is chosen with `model_mock_behavior` (see app.model.mock).
    model_provider: Literal["openrouter", "bedrock", "mock"] = "mock"
    model_mock_behavior: Literal[
        "complete", "clarify", "clarify_no_dominant", "unavailable", "malformed"
    ] = "complete"
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_url: str = "https://openrouter.ai/api/v1/chat/completions"
    bedrock_model_id: str | None = None
    # Bedrock has no effort knob; the effort becomes a thinking-token budget.
    bedrock_thinking_budget_medium: int = 1024
    bedrock_thinking_budget_high: int = 4096
    model_connect_timeout_seconds: float = 2.0
    model_timeout_seconds: float = 8.0
    model_max_attempts: int = 2
    model_retry_base_delay_seconds: float = 0.2
    model_retry_max_delay_seconds: float = 1.0
    model_breaker_failure_threshold: int = 5
    model_breaker_reset_seconds: float = 30.0
    model_bulkhead_size: int = 4
    model_max_output_tokens: int = 1500
    model_photo_max_bytes: int = 5_000_000

    photos_bucket: str = "growpath-photos"
    # Presigned URLs are opened by the client, not by the service, so they may need a host
    # that differs from the one the service itself uses to reach the object store.
    photos_public_endpoint_url: str | None = None
    photo_url_ttl_seconds: int = 900

    feed_default_page_size: int = 20
    feed_max_page_size: int = 100

    @model_validator(mode="after")
    def _require_acceptance_window(self) -> Self:
        if not 0 < self.escalation_ack_margin_seconds < self.expert_reservation_ttl_seconds:
            raise ValueError("the ack margin must be positive and shorter than the reservation TTL")
        return self

    @model_validator(mode="after")
    def _require_provider_settings(self) -> Self:
        if self.model_provider == "openrouter":
            key = self.openrouter_api_key
            if key is None or not key.get_secret_value():
                raise ValueError("PLANT_CARE_OPENROUTER_API_KEY is required for openrouter")
            if not self.openrouter_model:
                raise ValueError("PLANT_CARE_OPENROUTER_MODEL is required for openrouter")
        if self.model_provider == "bedrock" and not self.bedrock_model_id:
            raise ValueError("PLANT_CARE_BEDROCK_MODEL_ID is required for bedrock")
        return self
