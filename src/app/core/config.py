from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Knowledge Extractor API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    cors_allow_origins: str = "*"
    github_personal_access_token: str | None = None
    tradingview_api_base_url: AnyHttpUrl = "https://tradingview-data1.p.rapidapi.com"
    tradingview_rapidapi_host: str = "tradingview-data1.p.rapidapi.com"
    tradingview_rapidapi_key: str | None = None
    tradingview_currency_pairs_cache_ttl_seconds: int = Field(default=86400, ge=0)
    tradingview_bulk_page_delay_seconds: float = Field(default=1.0, ge=0)
    tradingview_price_request_delay_seconds: float = Field(default=1.0, ge=0)
    tradingview_price_request_max_retries: int = Field(default=2, ge=0)
    tradingview_price_request_retry_delay_seconds: float = Field(default=10.0, ge=0)
    tradingview_price_request_max_candles_per_request: int = Field(default=1500, ge=1)
    analysis_webhook_url: AnyHttpUrl = (
        "https://mattdavis207.app.n8n.cloud/webhook/"
        "2b0a8594-f02c-4528-a30b-1971fa379fd9"
    )
    analysis_webhook_test_url: AnyHttpUrl = (
        "https://mattdavis207.app.n8n.cloud/webhook-test/"
        "2b0a8594-f02c-4528-a30b-1971fa379fd9"
    )
    jsonplaceholder_base_url: AnyHttpUrl = "https://jsonplaceholder.typicode.com"
    youtube_webshare_proxy_username: str | None = None
    youtube_webshare_proxy_password: str | None = None
    youtube_webshare_proxy_locations: str | None = None
    youtube_http_proxy_url: str | None = None
    youtube_https_proxy_url: str | None = None
    cloudinary_url: str | None = None 

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
