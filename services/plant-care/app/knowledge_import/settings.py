from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.knowledge_import.source import FetchPolicy


class FetchSettings(BaseSettings):
    """The polite-fetching limits, all a command that only reads PlantSolve needs."""

    model_config = SettingsConfigDict(env_prefix="PLANT_CARE_")

    service_name: str = "plant-care-kb-import"
    log_level: str = "INFO"

    kb_import_pause_seconds: float = Field(default=2.0, ge=0)
    kb_import_timeout_seconds: float = Field(default=10.0, gt=0)
    kb_import_max_response_bytes: int = Field(default=2_000_000, gt=0)
    kb_import_max_attempts: int = Field(default=3, ge=1)
    kb_import_retry_base_delay_seconds: float = Field(default=1.0, ge=0)
    # Also caps a Retry-After the host asks for.
    kb_import_retry_max_delay_seconds: float = Field(default=30.0, ge=0)

    def fetch_policy(self, pause_seconds: float | None = None) -> FetchPolicy:
        return FetchPolicy(
            pause_seconds=self.kb_import_pause_seconds if pause_seconds is None else pause_seconds,
            max_response_bytes=self.kb_import_max_response_bytes,
            max_attempts=self.kb_import_max_attempts,
            retry_base_delay_seconds=self.kb_import_retry_base_delay_seconds,
            retry_max_delay_seconds=self.kb_import_retry_max_delay_seconds,
        )


class ImportSettings(FetchSettings):
    """What the import needs: the fetching limits and the database."""

    database_url: SecretStr
