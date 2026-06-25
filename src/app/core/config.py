from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Knowledge Extractor API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    jsonplaceholder_base_url: AnyHttpUrl = "https://jsonplaceholder.typicode.com"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
