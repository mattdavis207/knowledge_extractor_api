from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()

CurrencyPairAssetClass = Literal["forex", "futures"]

LEADERBOARD_ENDPOINTS: dict[CurrencyPairAssetClass, str] = {
    "forex": "/api/leaderboard/forex",
    "futures": "/api/leaderboard/futures",
}
DEFAULT_TABS: dict[CurrencyPairAssetClass, str] = {
    "forex": "all",
    "futures": "currencies",
}
VALID_TABS: dict[CurrencyPairAssetClass, set[str]] = {
    "forex": {
        "all",
        "major",
        "minor",
        "exotic",
        "americas",
        "europe",
        "asia",
        "pacific",
        "middle_east",
        "africa",
    },
    "futures": {
        "all",
        "agricultural",
        "energy",
        "currencies",
        "metals",
        "world_indices",
        "interest_rates",
    },
}
PAGE_SIZE = 150


class TradingViewCurrencyPair(BaseModel):
    symbol: str
    exchange: str | None = None
    ticker: str | None = None
    label: str
    description: str | None = None
    asset_type: str | None = None


class TradingViewCurrencyPairsResponse(BaseModel):
    asset_class: CurrencyPairAssetClass
    tab: str
    count: int
    symbols: list[str]
    currency_pairs: list[TradingViewCurrencyPair]


def get_tradingview_headers() -> dict[str, str]:
    if not settings.tradingview_rapidapi_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TRADINGVIEW_RAPIDAPI_KEY must be configured.",
        )

    return {
        "x-rapidapi-host": settings.tradingview_rapidapi_host,
        "x-rapidapi-key": settings.tradingview_rapidapi_key,
    }


def normalize_tradingview_symbol(row: dict) -> TradingViewCurrencyPair | None:
    symbol = row.get("symbol")
    exchange = row.get("exchange")
    ticker = row.get("name")

    if not symbol and exchange and ticker:
        symbol = f"{exchange}:{ticker}"

    if not isinstance(symbol, str) or ":" not in symbol:
        return None

    if not exchange or not ticker:
        exchange_part, ticker_part = symbol.split(":", maxsplit=1)
        exchange = exchange or exchange_part
        ticker = ticker or ticker_part

    description = row.get("description")
    label_source = description or ticker or symbol

    return TradingViewCurrencyPair(
        symbol=symbol,
        exchange=exchange,
        ticker=ticker,
        label=f"{label_source} ({symbol})",
        description=description,
        asset_type=row.get("type"),
    )


async def fetch_currency_pairs_from_leaderboard(
    *,
    asset_class: CurrencyPairAssetClass,
    tab: str,
    lang: str,
) -> list[TradingViewCurrencyPair]:
    endpoint = LEADERBOARD_ENDPOINTS[asset_class]
    headers = get_tradingview_headers()
    base_url = str(settings.tradingview_api_base_url).rstrip("/")
    pairs_by_symbol: dict[str, TradingViewCurrencyPair] = {}
    start = 0
    total_count: int | None = None

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        while total_count is None or start < total_count:
            response = await client.get(
                f"{base_url}{endpoint}",
                headers=headers,
                params={
                    "tab": tab,
                    "columnset": "overview",
                    "start": start,
                    "count": PAGE_SIZE,
                    "lang": lang,
                },
            )
            response.raise_for_status()
            payload = response.json()

            if not payload.get("success", False):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=payload.get("msg") or "TradingView API request failed.",
                )

            data = payload.get("data") or {}
            rows = data.get("data") or []
            total_count = data.get("totalCount", len(rows))

            for row in rows:
                pair = normalize_tradingview_symbol(row)
                if pair:
                    pairs_by_symbol[pair.symbol] = pair

            if len(rows) < PAGE_SIZE:
                break

            start += PAGE_SIZE

    return list(pairs_by_symbol.values())


@router.get(
    "/currency-pairs",
    response_model=TradingViewCurrencyPairsResponse,
    summary="List TradingView currency pair symbols for form options",
)
async def list_currency_pairs(
    asset_class: Annotated[
        CurrencyPairAssetClass,
        Query(description="Use forex for spot currency pairs or futures for currency futures."),
    ] = "forex",
    tab: Annotated[
        str | None,
        Query(
            description=(
                "TradingView leaderboard tab. Defaults to all for forex and currencies for futures."
            )
        ),
    ] = None,
    lang: Annotated[str, Query(min_length=2, max_length=12)] = "en",
) -> TradingViewCurrencyPairsResponse:
    selected_tab = tab or DEFAULT_TABS[asset_class]
    if selected_tab not in VALID_TABS[asset_class]:
        valid_tabs = ", ".join(sorted(VALID_TABS[asset_class]))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tab '{selected_tab}' for {asset_class}. Valid tabs: {valid_tabs}.",
        )

    try:
        currency_pairs = await fetch_currency_pairs_from_leaderboard(
            asset_class=asset_class,
            tab=selected_tab,
            lang=lang,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"TradingView API returned HTTP {exc.response.status_code}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="TradingView API request failed.",
        ) from exc

    return TradingViewCurrencyPairsResponse(
        asset_class=asset_class,
        tab=selected_tab,
        count=len(currency_pairs),
        symbols=[pair.symbol for pair in currency_pairs],
        currency_pairs=currency_pairs,
    )
