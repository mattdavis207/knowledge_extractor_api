from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Knowledge Extractor API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    github_personal_access_token: str | None = None
    tradingview_api_base_url: AnyHttpUrl = "https://tradingview-data1.p.rapidapi.com"
    tradingview_rapidapi_host: str = "tradingview-data1.p.rapidapi.com"
    tradingview_rapidapi_key: str | None = None
    jsonplaceholder_base_url: AnyHttpUrl = "https://jsonplaceholder.typicode.com"
    youtube_webshare_proxy_username: str | None = None
    youtube_webshare_proxy_password: str | None = None
    youtube_webshare_proxy_locations: str | None = None
    youtube_http_proxy_url: str | None = None
    youtube_https_proxy_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
