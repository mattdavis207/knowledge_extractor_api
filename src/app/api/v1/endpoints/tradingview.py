import asyncio
import math
import time
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()

CurrencyPairAssetClass = Literal["forex", "futures"]

PRICE_ENDPOINT: str = "/api/price"
PRICE_TIMEFRAME_SECONDS: dict[str, int] = {
    "1": 60,
    "5": 5 * 60,
    "15": 15 * 60,
    "30": 30 * 60,
    "60": 60 * 60,
    "240": 240 * 60,
}

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
CurrencyPairsCacheKey = tuple[CurrencyPairAssetClass, str, str, int, int, bool]
currency_pairs_cache: dict[
    CurrencyPairsCacheKey,
    tuple[float, list["TradingViewCurrencyPair"], int | None],
] = {}
COMMON_CURRENCY_PAIRS: dict[CurrencyPairAssetClass, list[dict[str, str]]] = {
    "forex": [
        {"symbol": "FX:EURUSD", "description": "Euro / U.S. Dollar"},
        {"symbol": "FX:GBPUSD", "description": "British Pound / U.S. Dollar"},
        {"symbol": "FX:USDJPY", "description": "U.S. Dollar / Japanese Yen"},
        {"symbol": "FX:USDCHF", "description": "U.S. Dollar / Swiss Franc"},
        {"symbol": "FX:AUDUSD", "description": "Australian Dollar / U.S. Dollar"},
        {"symbol": "FX:USDCAD", "description": "U.S. Dollar / Canadian Dollar"},
        {"symbol": "FX:NZDUSD", "description": "New Zealand Dollar / U.S. Dollar"},
        {"symbol": "FX:EURGBP", "description": "Euro / British Pound"},
        {"symbol": "FX:EURJPY", "description": "Euro / Japanese Yen"},
        {"symbol": "FX:GBPJPY", "description": "British Pound / Japanese Yen"},
        {"symbol": "FX:AUDJPY", "description": "Australian Dollar / Japanese Yen"},
        {"symbol": "FX:CADJPY", "description": "Canadian Dollar / Japanese Yen"},
        {"symbol": "FX:CHFJPY", "description": "Swiss Franc / Japanese Yen"},
        {"symbol": "FX:EURAUD", "description": "Euro / Australian Dollar"},
        {"symbol": "FX:EURCAD", "description": "Euro / Canadian Dollar"},
        {"symbol": "FX:GBPAUD", "description": "British Pound / Australian Dollar"},
        {"symbol": "FX:GBPCAD", "description": "British Pound / Canadian Dollar"},
        {"symbol": "FX:AUDCAD", "description": "Australian Dollar / Canadian Dollar"},
        {"symbol": "FX:AUDNZD", "description": "Australian Dollar / New Zealand Dollar"},
        {"symbol": "FX:NZDJPY", "description": "New Zealand Dollar / Japanese Yen"},
    ],
    "futures": [
        {"symbol": "CME:6E1!", "description": "Euro FX Futures"},
        {"symbol": "CME:6B1!", "description": "British Pound Futures"},
        {"symbol": "CME:6J1!", "description": "Japanese Yen Futures"},
        {"symbol": "CME:6A1!", "description": "Australian Dollar Futures"},
        {"symbol": "CME:6C1!", "description": "Canadian Dollar Futures"},
        {"symbol": "CME:6S1!", "description": "Swiss Franc Futures"},
        {"symbol": "CME:6N1!", "description": "New Zealand Dollar Futures"},
        {"symbol": "CME:6M1!", "description": "Mexican Peso Futures"},
    ],
}


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
    cached: bool
    source: Literal["tradingview", "fallback"]
    all_requested: bool
    pages_fetched: int
    start: int
    limit: int
    total_count: int | None = None
    symbols: list[str]
    currency_pairs: list[TradingViewCurrencyPair]


def parse_analysis_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def end_date_to_target_timestamp(end_date: str | date | datetime) -> int:
    parsed_end_date = parse_analysis_date(end_date)
    end_of_day_utc = datetime.combine(
        parsed_end_date,
        datetime_time(23, 59, 59),
        tzinfo=UTC,
    )
    return int(end_of_day_utc.timestamp())


def calculate_candles_range(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    timeframe: str,
) -> int:
    parsed_start_date = parse_analysis_date(start_date)
    parsed_end_date = parse_analysis_date(end_date)

    if parsed_end_date < parsed_start_date:
        raise ValueError("end_date must be on or after start_date")

    timeframe = str(timeframe)
    inclusive_days = (parsed_end_date - parsed_start_date).days + 1

    if timeframe == "D":
        return inclusive_days

    if timeframe == "W":
        return math.ceil(inclusive_days / 7)

    if timeframe == "M":
        return (
            (parsed_end_date.year - parsed_start_date.year) * 12
            + parsed_end_date.month
            - parsed_start_date.month
            + 1
        )

    if timeframe not in PRICE_TIMEFRAME_SECONDS:
        raise ValueError(f"Unsupported TradingView price timeframe: {timeframe}")

    seconds_in_range = inclusive_days * 24 * 60 * 60
    return math.ceil(seconds_in_range / PRICE_TIMEFRAME_SECONDS[timeframe])


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
    start: int,
    count: int,
) -> tuple[list[TradingViewCurrencyPair], int | None]:
    endpoint = LEADERBOARD_ENDPOINTS[asset_class]
    headers = get_tradingview_headers()
    base_url = str(settings.tradingview_api_base_url).rstrip("/")
    pairs_by_symbol: dict[str, TradingViewCurrencyPair] = {}

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(
            f"{base_url}{endpoint}",
            headers=headers,
            params={
                "tab": tab,
                "columnset": "overview",
                "start": start,
                "count": count,
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

    return list(pairs_by_symbol.values()), total_count


async def fetch_all_currency_pairs_from_leaderboard(
    *,
    asset_class: CurrencyPairAssetClass,
    tab: str,
    lang: str,
    start: int,
    count: int,
    page_delay_seconds: float,
) -> tuple[list[TradingViewCurrencyPair], int | None, int]:
    all_pairs_by_symbol: dict[str, TradingViewCurrencyPair] = {}
    current_start = start
    total_count: int | None = None
    pages_fetched = 0

    while total_count is None or current_start < total_count:
        pairs, total_count = await fetch_currency_pairs_from_leaderboard(
            asset_class=asset_class,
            tab=tab,
            lang=lang,
            start=current_start,
            count=count,
        )
        pages_fetched += 1

        for pair in pairs:
            all_pairs_by_symbol[pair.symbol] = pair

        if len(pairs) < count:
            break

        current_start += count
        if total_count is not None and current_start >= total_count:
            break

        if page_delay_seconds > 0:
            await asyncio.sleep(page_delay_seconds)

    return list(all_pairs_by_symbol.values()), total_count, pages_fetched


def get_fallback_currency_pairs(
    asset_class: CurrencyPairAssetClass,
) -> list[TradingViewCurrencyPair]:
    pairs = []
    for row in COMMON_CURRENCY_PAIRS[asset_class]:
        pair = normalize_tradingview_symbol(
            {
                "symbol": row["symbol"],
                "description": row["description"],
                "type": asset_class,
            }
        )
        if pair:
            pairs.append(pair)
    return pairs


def get_cached_currency_pairs(
    key: CurrencyPairsCacheKey,
) -> tuple[list[TradingViewCurrencyPair], int | None] | None:
    cached = currency_pairs_cache.get(key)
    if not cached:
        return None

    expires_at, pairs, total_count = cached
    if expires_at <= time.monotonic():
        currency_pairs_cache.pop(key, None)
        return None

    return pairs, total_count


def set_cached_currency_pairs(
    key: CurrencyPairsCacheKey,
    pairs: list[TradingViewCurrencyPair],
    total_count: int | None,
) -> None:
    ttl_seconds = settings.tradingview_currency_pairs_cache_ttl_seconds
    if ttl_seconds <= 0:
        return

    currency_pairs_cache[key] = (time.monotonic() + ttl_seconds, pairs, total_count)


def raise_tradingview_http_error(exc: httpx.HTTPStatusError) -> None:
    if exc.response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        headers = {}
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            headers["Retry-After"] = retry_after

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "TradingView/RapidAPI rate limit reached. Wait for the quota window to reset, "
                "upgrade the RapidAPI plan, or retry this endpoint after cached data is available."
            ),
            headers=headers or None,
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"TradingView API returned HTTP {exc.response.status_code}.",
    ) from exc


@router.get(
    "/currency-pairs",
    response_model=TradingViewCurrencyPairsResponse,
    summary="List TradingView currency pair symbols for form options",
)
async def list_currency_pairs(
    response: Response,
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
    refresh: Annotated[
        bool,
        Query(description="Bypass the in-memory cache and fetch fresh data from TradingView."),
    ] = False,
    start: Annotated[int, Query(ge=0)] = 0,
    count: Annotated[int, Query(ge=1, le=PAGE_SIZE)] = PAGE_SIZE,
    all: Annotated[
        bool,
        Query(description="Fetch every page from TradingView, sleeping between upstream calls."),
    ] = False,
    page_delay_seconds: Annotated[
        float | None,
        Query(
            ge=0,
            le=10,
            description="Delay between upstream page calls when all=true.",
        ),
    ] = None,
) -> TradingViewCurrencyPairsResponse:
    selected_tab = tab or DEFAULT_TABS[asset_class]
    if selected_tab not in VALID_TABS[asset_class]:
        valid_tabs = ", ".join(sorted(VALID_TABS[asset_class]))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tab '{selected_tab}' for {asset_class}. Valid tabs: {valid_tabs}.",
        )

    cache_key: CurrencyPairsCacheKey = (asset_class, selected_tab, lang, start, count, all)
    cached = False
    source: Literal["tradingview", "fallback"] = "tradingview"
    total_count: int | None = None
    pages_fetched = 0
    delay_seconds = (
        settings.tradingview_bulk_page_delay_seconds
        if page_delay_seconds is None
        else page_delay_seconds
    )
    cached_pairs = None if refresh else get_cached_currency_pairs(cache_key)

    if cached_pairs is None:
        try:
            if all:
                (
                    currency_pairs,
                    total_count,
                    pages_fetched,
                ) = await fetch_all_currency_pairs_from_leaderboard(
                    asset_class=asset_class,
                    tab=selected_tab,
                    lang=lang,
                    start=start,
                    count=count,
                    page_delay_seconds=delay_seconds,
                )
            else:
                currency_pairs, total_count = await fetch_currency_pairs_from_leaderboard(
                    asset_class=asset_class,
                    tab=selected_tab,
                    lang=lang,
                    start=start,
                    count=count,
                )
                pages_fetched = 1
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                fallback_pairs = get_fallback_currency_pairs(asset_class)
                total_count = len(fallback_pairs)
                currency_pairs = fallback_pairs[start : start + count]
                source = "fallback"
                pages_fetched = 0
                response.status_code = status.HTTP_200_OK
                response.headers["X-TradingView-Upstream-Status"] = "429"
            else:
                raise_tradingview_http_error(exc)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="TradingView API request failed.",
            ) from exc
        set_cached_currency_pairs(cache_key, currency_pairs, total_count)
    else:
        currency_pairs, total_count = cached_pairs
        cached = True
        response.headers["X-Cache"] = "HIT"
        pages_fetched = 0

    return TradingViewCurrencyPairsResponse(
        asset_class=asset_class,
        tab=selected_tab,
        count=len(currency_pairs),
        cached=cached,
        source=source,
        all_requested=all,
        pages_fetched=pages_fetched,
        start=start,
        limit=count,
        total_count=total_count,
        symbols=[pair.symbol for pair in currency_pairs],
        currency_pairs=currency_pairs,
    )



@router.get(
    "/price-data",
    summary="Get TradingView price candles for a symbol and date range",
)
async def get_price_data(
    assets: Annotated[list[str], Query(min_length=1)],
    timeframe: str,
    sd: str,
    ed: str,
) -> dict:
    endpoint = PRICE_ENDPOINT
    headers = get_tradingview_headers()
    base_url = str(settings.tradingview_api_base_url).rstrip("/")

    try:
        candle_range = calculate_candles_range(sd, ed, timeframe)
        target_timestamp = end_date_to_target_timestamp(ed)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    
    output = {}

    for asset in assets:

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(
                f"{base_url}{endpoint}/{quote(asset, safe='')}",
                headers=headers,
                params={
                    "timeframe": timeframe,
                    "range": candle_range,
                    "to": target_timestamp,
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
            history = data.get("history") or []
            
            output[asset] = {
                "symbol": asset,
                "timeframe": timeframe,
                "start_date": sd,
                "end_date": ed,
                "range": candle_range,
                "to": target_timestamp,
                "count": len(history),
                "history": history,
            }

    return output
