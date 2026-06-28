import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.api.v1.router import api_router
from app.core.config import settings

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
CURRENCY_PAIRS_PATH = PROJECT_ROOT / "currency_pairs.json"


def get_cors_allow_origins() -> list[str]:
    if settings.cors_allow_origins.strip() == "*":
        return ["*"]

    return [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]


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

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    return app


app = create_app()
