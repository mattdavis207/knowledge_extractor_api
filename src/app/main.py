import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.api.v1.router import api_router
from app.core.config import settings

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
CURRENCY_PAIRS_PATH = PROJECT_ROOT / "currency_pairs.json"
EXCHANGES_PATH = PROJECT_ROOT / "exchanges.json"


def get_cors_allow_origins() -> list[str]:
    if settings.cors_allow_origins.strip() == "*":
        return ["*"]

    return [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]


async def post_analysis_webhook(payload: dict[str, Any]) -> int:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(str(settings.analysis_webhook_url), json=payload)
    response.raise_for_status()
    return response.status_code


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

    @app.get("/exchanges.json", tags=["local-data"])
    async def exchanges() -> dict:
        with EXCHANGES_PATH.open(encoding="utf-8") as file:
            return json.load(file)

    @app.post("/analysis-webhook", tags=["analysis"])
    async def analysis_webhook(payload: dict[str, Any]) -> dict[str, int | str]:
        try:
            webhook_status_code = await post_analysis_webhook(payload)
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

        return {
            "status": "sent",
            "webhook_status_code": webhook_status_code,
        }

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    return app


app = create_app()
