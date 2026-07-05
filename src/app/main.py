import json
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.api.v1.router import api_router
from app.core.config import settings

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
CURRENCY_PAIRS_PATH = PROJECT_ROOT / "currency_pairs.json"
TRADING_ASSETS_PATH = PROJECT_ROOT / "trading_assets.json"
EXCHANGES_PATH = PROJECT_ROOT / "exchanges.json"
WebhookEnvironment = Literal["prod", "test"]


def get_cors_allow_origins() -> list[str]:
    if settings.cors_allow_origins.strip() == "*":
        return ["*"]

    return [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]


def get_analysis_webhook_url(webhook_environment: WebhookEnvironment) -> str:
    if webhook_environment == "test":
        return str(settings.analysis_webhook_test_url)

    return str(settings.analysis_webhook_url)


async def post_analysis_webhook(
    payload: dict[str, Any],
    webhook_environment: WebhookEnvironment = "prod",
) -> tuple[int, Any]:
    webhook_url = get_analysis_webhook_url(webhook_environment)
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(webhook_url, json=payload)
    response.raise_for_status()

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = response.text

    return response.status_code, response_payload


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_allow_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    templates = Jinja2Templates(directory=APP_DIR / "templates")

    @app.get("/")
    async def home(request: Request):
        return templates.TemplateResponse(
            request,
            "templates.html",
        )

    @app.get("/currency-pairs.json", tags=["local-data"])
    async def currency_pairs() -> dict:
        with CURRENCY_PAIRS_PATH.open(encoding="utf-8") as file:
            return json.load(file)

    @app.get("/trading-assets.json", tags=["local-data"])
    async def trading_assets() -> dict:
        with TRADING_ASSETS_PATH.open(encoding="utf-8") as file:
            return json.load(file)

    @app.get("/exchanges.json", tags=["local-data"])
    async def exchanges() -> dict:
        with EXCHANGES_PATH.open(encoding="utf-8") as file:
            return json.load(file)

    @app.post("/analysis-webhook", tags=["analysis"])
    async def analysis_webhook(
        payload: dict[str, Any],
        webhook_environment: Annotated[
            WebhookEnvironment,
            Query(description="Use prod for the production n8n webhook or test for webhook-test."),
        ] = "prod",
    ) -> dict[str, Any]:
        try:
            webhook_status_code, webhook_response = await post_analysis_webhook(
                payload,
                webhook_environment,
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Analysis webhook returned HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Analysis webhook request failed.",
            ) from exc

        response_payload: dict[str, Any] = {
            "status": "sent",
            "webhook_status_code": webhook_status_code,
            "webhook_response": webhook_response,
        }

        if isinstance(webhook_response, dict) and webhook_response.get("execution_url"):
            response_payload["execution_url"] = webhook_response["execution_url"]

        return response_payload

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    return app


app = create_app()
