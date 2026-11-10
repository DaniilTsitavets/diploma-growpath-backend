from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMMERCE_")

    service_name: str = "commerce"
    log_level: str = "INFO"
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
