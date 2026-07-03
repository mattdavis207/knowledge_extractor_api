import asyncio
import time
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.api.v1.tradingview.constants import (
    COMMON_CURRENCY_PAIRS,
    DEFAULT_TABS,
    LEADERBOARD_ENDPOINTS,
    METADATA_EXCHANGES_ENDPOINT,
    PAGE_SIZE,
    PRICE_ENDPOINT,
    SCREENSHOT_PADDING_WINDOW_WEEKS,
    VALID_TABS,
    AnalysisDirection,
    AnalysisType,
    BiasType,
    CurrencyPairAssetClass,
    CurrencyPairsCacheKey,
    PriceCandleType,
)
from app.api.v1.tradingview.schemas import (
    AnalysisCaseResult,
    ParsedTradingViewAsset,
    PriceDataInput,
    TradingViewAnalysisExecution,
    TradingViewAnalyzeRequest,
    TradingViewAnalyzeResponse,
    TradingViewAssetAnalyzeResult,
    TradingViewAssetRequest,
    TradingViewCurrencyPair,
    TradingViewCurrencyPairsResponse,
    TradingViewExchangesResponse,
    TradingViewPriceCandle,
    TradingViewPriceData,
    TradingViewPriceDataRequest,
    TradingViewPriceDataResponse,
)
from app.api.v1.tradingview.screenshots import (
    plot_to_png_buffer,
    transform_candles_to_dataframe,
    upload_chart_image,
)
from app.api.v1.tradingview.utils import (
    add_high_low_order_to_parent_candles,
    build_tradingview_symbol,
    calculate_candles_range,
    calculate_hourly_range_from_timestamps,
    end_date_to_target_timestamp,
    extract_metadata_list,
    filter_candles_to_timestamp_range,
    find_high_before_low,  # noqa: F401
    get_candle_time,
    get_context_date_range,
    get_price_data_for_analysis_asset,
    normalize_price_data_timeframe,
    normalize_tradingview_symbol,
    parse_body_price_assets,
    parse_query_price_assets,
    parse_symbol_parts,
    price_data_to_histories,
    price_data_to_keyed_items,
    start_date_to_target_timestamp,
    unix_seconds_to_date,
    unix_seconds_to_utc_datetime,  # noqa: F401
)
from app.core.config import settings

router = APIRouter()


currency_pairs_cache: dict[
    CurrencyPairsCacheKey,
    tuple[float, list["TradingViewCurrencyPair"], int | None],
] = {}

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


async def fetch_tradingview_metadata_list(endpoint: str, key: str) -> list[object]:
    headers = get_tradingview_headers()
    base_url = str(settings.tradingview_api_base_url).rstrip("/")

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(f"{base_url}{endpoint}", headers=headers)
        response.raise_for_status()
        payload = response.json()

    if isinstance(payload, dict) and payload.get("success") is False:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=payload.get("msg") or "TradingView API request failed.",
        )

    return extract_metadata_list(payload, key)


async def get_tradingview_exchanges_response() -> TradingViewExchangesResponse:
    try:
        exchanges = await fetch_tradingview_metadata_list(METADATA_EXCHANGES_ENDPOINT, "exchanges")
    except httpx.HTTPStatusError as exc:
        raise_tradingview_http_error(exc)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="TradingView API request failed.",
        ) from exc

    return TradingViewExchangesResponse(count=len(exchanges), exchanges=exchanges)


@router.get(
    "/exchanges",
    summary="Get exchanges supported by the TradingView Data API.",
    response_model=TradingViewExchangesResponse,
)
async def list_exchanges() -> TradingViewExchangesResponse:
    return await get_tradingview_exchanges_response()


@router.get(
    "/metadata/exchanges",
    summary="Get exchanges supported by the TradingView Data API.",
    response_model=TradingViewExchangesResponse,
)
async def get_exchanges() -> TradingViewExchangesResponse:
    return await get_tradingview_exchanges_response()


@router.get(
    "/metadata",
    summary="Get TradingView exchange metadata.",
    response_model=TradingViewExchangesResponse,
)
async def get_meta_data() -> TradingViewExchangesResponse:
    return await get_tradingview_exchanges_response()


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


async def fetch_tradingview_price_history(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    asset: str,
    timeframe: str,
    candle_type: PriceCandleType,
    candle_range: int,
    target_timestamp: int,
) -> list[dict]:
    response = await client.get(
        f"{base_url}{PRICE_ENDPOINT}/{quote(asset, safe='')}",
        headers=headers,
        params={
            "timeframe": timeframe,
            "type": candle_type,
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
    if not isinstance(history, list):
        return []

    return history


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


async def get_price_data_for_assets(
    *,
    parsed_assets: list[ParsedTradingViewAsset],
    timeframe: str,
    candle_type: PriceCandleType,
    parsed_start_date: str,
    parsed_end_date: str,
    analysis_type: AnalysisType | None,
    include_hourly_candles: bool,
    request_delay_seconds: float | None,
) -> dict[str, TradingViewPriceData]:
    headers = get_tradingview_headers()
    base_url = str(settings.tradingview_api_base_url).rstrip("/")
    delay_seconds = (
        settings.tradingview_price_request_delay_seconds
        if request_delay_seconds is None
        else request_delay_seconds
    )

    try:
        candle_range = calculate_candles_range(
            parsed_start_date,
            parsed_end_date,
            timeframe,
        )
        context_start_date, context_end_date = get_context_date_range(
            parsed_start_date,
            parsed_end_date,
            analysis_type,
        )
        context_candle_range = calculate_candles_range(
            context_start_date,
            context_end_date,
            timeframe,
        )
        start_timestamp = start_date_to_target_timestamp(parsed_start_date)
        target_timestamp = end_date_to_target_timestamp(parsed_end_date)
        context_start_timestamp = start_date_to_target_timestamp(context_start_date)
        context_target_timestamp = end_date_to_target_timestamp(context_end_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    output: dict[str, TradingViewPriceData] = {}
    upstream_request_count = 0

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        for asset in parsed_assets:
            if upstream_request_count > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            history = await fetch_tradingview_price_history(
                client=client,
                base_url=base_url,
                headers=headers,
                asset=asset.symbol,
                timeframe=timeframe,
                candle_type=candle_type,
                candle_range=context_candle_range,
                target_timestamp=context_target_timestamp,
            )
            upstream_request_count += 1
            context_history = history
            history = filter_candles_to_timestamp_range(
                context_history,
                start_timestamp,
                target_timestamp,
            )

            hourly_candles = []
            hourly_to = None
            hourly_range = None

            # get hourly candles if included
            if include_hourly_candles:
                hourly_to = context_target_timestamp
                parent_candle_times = [
                    candle_time
                    for candle in context_history
                    if (candle_time := get_candle_time(candle)) is not None
                ]
                hourly_start_timestamp = (
                    min(parent_candle_times) if parent_candle_times else context_start_timestamp
                )
                hourly_range = calculate_hourly_range_from_timestamps(
                    hourly_start_timestamp,
                    context_target_timestamp,
                )
                if upstream_request_count > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

                hourly_history = await fetch_tradingview_price_history(
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    asset=asset.symbol,
                    timeframe="60",
                    candle_type=candle_type,
                    candle_range=hourly_range,
                    target_timestamp=context_target_timestamp,
                )
                upstream_request_count += 1
                hourly_candles = filter_candles_to_timestamp_range(
                    hourly_history,
                    hourly_start_timestamp,
                    context_target_timestamp,
                )
                context_history = add_high_low_order_to_parent_candles(
                    parent_candles=context_history,
                    hourly_candles=hourly_candles,
                    timeframe=timeframe,
                    target_timestamp=context_target_timestamp,
                )
                history = filter_candles_to_timestamp_range(
                    context_history,
                    start_timestamp,
                    target_timestamp,
                )

            output[asset.response_key] = TradingViewPriceData(
                symbol=asset.symbol,
                pair=asset.pair,
                exchange=asset.exchange or None,
                bias=asset.bias,
                timeframe=timeframe,
                candle_type=candle_type,
                start_date=parsed_start_date,
                end_date=parsed_end_date,
                range=candle_range,
                to=target_timestamp,
                count=len(history),
                history=history,
                context_history=context_history,
                hourly_range=hourly_range,
                hourly_to=hourly_to,
                hourly_count=len(hourly_candles),
                hourly_candles=hourly_candles,
            )

    return output



@router.get(
    "/price-data",
    summary="Get TradingView price candles for a symbol and date range",
    response_model=dict[str, TradingViewPriceData],
)
async def get_price_data(
    request: Request,
    timeframe: str,
    candle_type: Annotated[
        PriceCandleType,
        Query(
            alias="type",
            description=(
                "TradingView candle type. Defaults to Japanese/native candles for chart parity."
            ),
        ),
    ] = "Japanese",
    assets: Annotated[list[str] | None, Query()] = None,
    sd: str | None = None,
    ed: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    analysis_type: AnalysisType | None = None,
    include_hourly_candles: Annotated[
        bool,
        Query(
            description=(
                "Also fetch 1-hour candles for the same date range so analysis can infer "
                "whether the weekly high or low formed first."
            )
        ),
    ] = False,
    request_delay_seconds: Annotated[
        float | None,
        Query(
            ge=0,
            description=(
                "Optional delay between upstream TradingView price requests. "
                "Defaults to TRADINGVIEW_PRICE_REQUEST_DELAY_SECONDS."
            ),
        ),
    ] = None,
) -> dict[str, TradingViewPriceData]:
    query_params = request.query_params
    raw_assets = assets or query_params.getlist("assets[]")

    if not raw_assets:
        raw_assets = [
            value
            for key, value in query_params.multi_items()
            if key.startswith("assets[")
        ]

    if not raw_assets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one assets query parameter is required.",
        )

    parsed_start_date = sd or start_date
    parsed_end_date = ed or end_date

    if not parsed_start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either sd or start_date is required.",
        )

    if not parsed_end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either ed or end_date is required.",
        )

    return await get_price_data_for_assets(
        parsed_assets=parse_query_price_assets(raw_assets),
        timeframe=timeframe,
        candle_type=candle_type,
        parsed_start_date=parsed_start_date,
        parsed_end_date=parsed_end_date,
        analysis_type=analysis_type,
        include_hourly_candles=include_hourly_candles,
        request_delay_seconds=request_delay_seconds,
    )


@router.post(
    "/price-data",
    summary="Get TradingView price candles for configured symbols and exchanges.",
    response_model=TradingViewPriceDataResponse,
)
async def post_price_data(
    payload: TradingViewPriceDataRequest,
) -> TradingViewPriceDataResponse:
    if not payload.assets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one asset is required.",
        )

    parsed_start_date = payload.sd or payload.start_date
    parsed_end_date = payload.ed or payload.end_date

    if not parsed_start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either sd or start_date is required.",
        )

    if not parsed_end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either ed or end_date is required.",
        )

    price_data = await get_price_data_for_assets(
        parsed_assets=parse_body_price_assets(payload.assets),
        timeframe=normalize_price_data_timeframe(payload.timeframe),
        candle_type=payload.candle_type,
        parsed_start_date=parsed_start_date,
        parsed_end_date=parsed_end_date,
        analysis_type=payload.analysis_type,
        include_hourly_candles=payload.include_hourly_candles,
        request_delay_seconds=payload.request_delay_seconds,
    )

    return TradingViewPriceDataResponse(price_data=price_data_to_keyed_items(price_data))


def is_bullish_potential_setup_tm(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
) -> bool:
    # candle 2 runs candle 1's low and closes within candle 1's range (non-inclusive lower bound)
    return curr.min < prev.min and (prev.min < curr.close <= prev.max)


def is_bearish_potential_setup_tm(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
) -> bool:
    # candle 2 runs candle 1's high and closes within candle 1's range (non-inclusive upper bound)
    return curr.max > prev.max and (prev.min <= curr.close < prev.max)


def evaluate_bullish_triple_m_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None
) -> AnalysisCaseResult:
    if not is_bullish_potential_setup_tm(prev, curr):
        return None, None, False
    
    target = prev.min + ((prev.max- prev.min) / 2)

    # Determine target

    # Scenario 1: candle 2 did not hit %50 of candle 1, target is still %50 of candle 1 
    if curr.max <= target:
        pass
    
    # Scenario 2: candle 2 already hit %50 of candle 1, target is high of candle 2
    elif curr.max > target: 
        target = curr.max
    

    # candle 3 hit the target and high came first.
    if next_candle.max > target and next_candle.high_before is True:
        return True, None, False
    # candle 3 hit the target and did not hit low of candle 2 (even though low came first)
    if next_candle.max > target and next_candle.min >= curr.min:
        return True, None, False 
     # candle 3 ran the second candle low before validation, so the setup failed immediately.
    if next_candle.min < curr.min:
        return False, None, False
    
    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, None, False
    
     # Third candle did not resolve the setup, so scan subsequent candles until consolidation breaks.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle high hit the target and high came first.
        if cons_candle.max > target and cons_candle.high_before is True:
            return True, cons_candle, True
        # Cons candle hit the target and did not hit low of candle 2 (even though low came first)
        if cons_candle.max > target and cons_candle.min >= curr.min:
            return True, cons_candle, True
        # Cons candle failed the setup
        if cons_candle.min < curr.min:
            return False, cons_candle, True

        j += 1

    return False, None, True



def evaluate_bearish_triple_m_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None
) -> AnalysisCaseResult:
    if not is_bearish_potential_setup_tm(prev, curr):
        return None, None, False

    target = prev.min + ((prev.max- prev.min) / 2)

    # Determine target

    # Scenario 1: candle 2 did not hit %50 of candle 1, target is still %50 of candle 1 
    if curr.min >= target:
        pass
    
    # Scenario 2: candle 2 already hit %50 of candle 1, target is high of candle 2
    elif curr.min < target: 
        target = curr.min
    

    # candle 3 hit the target and low came first.
    if next_candle.min < target and next_candle.high_before is False:
        return True, None, False
    # candle 3 hit the target and did not hit high of candle 2 (even though high came first)
    if next_candle.min < target and next_candle.max <= curr.max:
        return True, None, False 
     # candle 3 ran the second candle high before validation, so the setup failed immediately.
    if next_candle.max > curr.max:
        return False, None, False
    
    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, None, False
    
     # Third candle did not resolve the setup, so scan subsequent candles until consolidation breaks.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle hit the target and low came first.
        if cons_candle.min < target and cons_candle.high_before is False:
            return True, cons_candle, True
        # Cons candle hit the target and did not hit high of candle 2 (even though high came first)
        if cons_candle.min < target and cons_candle.max <= curr.max:
            return True, cons_candle, True
        # Cons candle failed the setup
        if cons_candle.max > curr.max:
            return False, cons_candle, True

        j += 1

    return False, None, True


def is_bullish_potential_setup_en(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
) -> bool:
    return curr.close >= curr.open and curr.close >= prev.max


def is_bearish_potential_setup_en(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
) -> bool:
    return curr.close < curr.open and curr.close <= prev.min


def evaluate_bullish_engulfing_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> AnalysisCaseResult:
    if not is_bullish_potential_setup_en(prev, curr):
        return None, None, False

    # Third candle high ran the second candle high and happened before the low occurred.
    if next_candle.max >= curr.max and next_candle.high_before is True:
        return True, None, False

    # Third candle high ran the second candle high without running the second candle low.
    if next_candle.max >= curr.max and next_candle.min >= curr.min:
        return True, None, False

    # Third candle ran the second candle low before validation, so the setup failed immediately.
    if next_candle.min < curr.min:
        return False, None, False

    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, None, False

    # Third candle did not resolve the setup, so scan subsequent candles until consolidation breaks.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle high ran the second high candle and happened before the low occurred
        if cons_candle.max >= curr.max and cons_candle.high_before is True:
            return True, cons_candle, True

        # Cons candle high ran the second candle high without running the second candle low
        if cons_candle.max >= curr.max and cons_candle.min >= curr.min:
            return True, cons_candle, True

        # Cons candle failed the setup
        if cons_candle.min < curr.min:
            return False, cons_candle, True

        j += 1

    return False, None, True


def evaluate_bearish_engulfing_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> AnalysisCaseResult:
    if not is_bearish_potential_setup_en(prev, curr):
        return None, None, False

    # Third candle low ran the second candle low and happened before the high occurred.
    if next_candle.min <= curr.min and next_candle.high_before is False:
        return True, None, False

    # Third candle low ran the second candle low without running the second candle high.
    if next_candle.min <= curr.min and next_candle.max <= curr.max:
        return True, None, False

    # Third candle ran the second candle high before validation, so the setup failed immediately.
    if next_candle.max > curr.max:
        return False, None, False

    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, None, False

    # Third candle did not resolve the setup, so scan subsequent candles until consolidation breaks.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle low ran the second candle low and happened before the high occurred.
        if cons_candle.min <= curr.min and cons_candle.high_before is False:
            return True, cons_candle, True

        # Cons candle low ran the second candle low without running the second candle high.
        if cons_candle.min <= curr.min and cons_candle.max <= curr.max:
            return True, cons_candle, True

        # Cons candle failed the setup by running the second candle high.
        if cons_candle.max > curr.max:
            return False, cons_candle, True

        j += 1

    return False, None, True


def get_directions_for_bias(bias: BiasType) -> list[AnalysisDirection]:
    if bias == "Bullish":
        return ["Bullish"]

    if bias == "Bearish":
        return ["Bearish"]

    return ["Bullish", "Bearish"]


def resolve_analysis_asset(
    pair_key: str,
    assets: dict[str, TradingViewAssetRequest],
    price_data: PriceDataInput,
) -> tuple[str, str | None, str | None, BiasType]:
    exchange_from_key, pair = parse_symbol_parts(pair_key)
    asset_config = assets.get(pair_key) or assets.get(pair)
    asset_price_data = get_price_data_for_analysis_asset(pair_key, pair, price_data)

    if asset_config:
        exchange = asset_config.exchange.strip().upper()
        bias = asset_config.bias
    elif asset_price_data:
        exchange = asset_price_data.exchange or exchange_from_key
        bias = asset_price_data.bias
    else:
        exchange = exchange_from_key
        bias = "Both"

    symbol = build_tradingview_symbol(exchange, pair) if exchange else pair

    return pair, exchange, symbol, bias


def generate_screenshot_url(
    asset: TradingViewAssetAnalyzeResult,
    execution: TradingViewAnalysisExecution,
) -> str | None:

    # extract execution bound times
    prev_date_unix_seconds = execution.prev.time
    next_date_unix_seconds = execution.next_candle.time

    analysis_type = asset.analysis_type
    context_history = sorted(asset.context_history, key=lambda candle: candle.time)

    # calculate padded start_date and end_date
    start_window_weeks, end_window_weeks = SCREENSHOT_PADDING_WINDOW_WEEKS[analysis_type]
    padded_start_date = prev_date_unix_seconds - (604800 * start_window_weeks)
    padded_end_date = next_date_unix_seconds + (604800 * end_window_weeks)
    
    start_index = next(
        (i for i, history_item in enumerate(context_history)
        if history_item.time >= padded_start_date),
        0,
    )

    end_index = next(
        (i for i, history_item in reversed(list(enumerate(context_history)))
        if history_item.time <= padded_end_date),
        len(context_history) - 1,
    )
    
    # slice the range of candles
    context_window = context_history[start_index: end_index+1]

    # transform data
    data = transform_candles_to_dataframe(context_window)

    prev_date = execution.prev_date.strftime("%Y-%m-%d")
    next_date = execution.next_date.strftime("%Y-%m-%d")

    # plot and save image to cloudinary
    buffer = plot_to_png_buffer(data, prev_date, next_date)
    public_id = f"{asset.pair}_{execution.direction}_{execution.curr.time}"
    
    return upload_chart_image(buffer, public_id)


def add_screenshot_urls_to_analysis(
    payload: TradingViewAnalyzeResponse,
) -> TradingViewAnalyzeResponse:
    for asset in payload.assets.values():
        for execution in asset.analysis_executions:
            execution.screenshot_url = generate_screenshot_url(asset, execution)

    return payload


@router.post(
    "/analyze",
    summary="Analyze TradingView historical price candles based on a specific analysis type.",
    response_model=TradingViewAnalyzeResponse,
)
async def analyze_price_data(
    payload: TradingViewAnalyzeRequest,
) -> TradingViewAnalyzeResponse:
    histories = payload.histories or payload.history or price_data_to_histories(payload.price_data)
    if not histories:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one history list is required.",
        )

    asset_results: dict[str, TradingViewAssetAnalyzeResult] = {}

    for pair_key, history in histories.items():
        pair, exchange, symbol, bias = resolve_analysis_asset(
            pair_key,
            payload.assets,
            payload.price_data,
        )
        sorted_history = sorted(history, key=lambda candle: candle.time)
        analysis_executions: list[TradingViewAnalysisExecution] = []

        # loop through 3 candle setup
        for i in range(1, len(sorted_history) - 1):
            prev = sorted_history[i - 1]
            curr = sorted_history[i]
            next_candle = sorted_history[i + 1]

            for direction in get_directions_for_bias(bias):
                execution_next_candle = next_candle
                consolidation = False
                # Bullish Engulfing Analysis
                if payload.analysis_type == "Bullish Engulfing" and direction == "Bullish":
                    success, replacement_next_candle, consolidation = (
                        evaluate_bullish_engulfing_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i,
                        )
                    )
                    if replacement_next_candle is not None:
                        execution_next_candle = replacement_next_candle
                # Bearish Engulfing Analysis
                elif payload.analysis_type == "Bullish Engulfing" and direction == "Bearish":
                    success, replacement_next_candle, consolidation = (
                        evaluate_bearish_engulfing_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i,
                        )
                    )
                    if replacement_next_candle is not None:
                        execution_next_candle = replacement_next_candle
                # Bullish Triple M Analysis
                elif payload.analysis_type == "Triple M" and direction == "Bullish":
                    success, replacement_next_candle, consolidation = (
                        evaluate_bullish_triple_m_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i
                        )
                    )
                    if replacement_next_candle is not None:
                        execution_next_candle = replacement_next_candle
                # Bearish Triple M Analysis
                elif payload.analysis_type == "Triple M" and direction == "Bearish":
                    success, replacement_next_candle, consolidation = (
                        evaluate_bearish_triple_m_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i
                        )
                    )
                    if replacement_next_candle is not None:
                        execution_next_candle = replacement_next_candle
                else:
                    success = None

                if success is None:
                    continue

                analysis_executions.append(
                    TradingViewAnalysisExecution(
                        pair=pair,
                        exchange=exchange,
                        symbol=symbol,
                        bias=bias,
                        analysis_type=payload.analysis_type,
                        direction=direction,
                        success=success,
                        consolidation=consolidation,
                        prev=prev,
                        curr=curr,
                        next_candle=execution_next_candle,
                        prev_date=unix_seconds_to_date(prev.time),
                        curr_date=unix_seconds_to_date(curr.time),
                        next_date=unix_seconds_to_date(execution_next_candle.time),
                    )
                )

        bullish_success_count = sum(
            1
            for execution in analysis_executions
            if execution.direction == "Bullish" and execution.success
        )
        bullish_failure_count = sum(
            1
            for execution in analysis_executions
            if execution.direction == "Bullish" and not execution.success
        )
        bearish_success_count = sum(
            1
            for execution in analysis_executions
            if execution.direction == "Bearish" and execution.success
        )
        bearish_failure_count = sum(
            1
            for execution in analysis_executions
            if execution.direction == "Bearish" and not execution.success
        )

        asset_results[pair_key] = TradingViewAssetAnalyzeResult(
            pair=pair,
            exchange=exchange,
            symbol=symbol,
            bias=bias,
            analysis_type=payload.analysis_type,
            bullish_success_count=bullish_success_count,
            bullish_failure_count=bullish_failure_count,
            bullish_total_count=bullish_success_count + bullish_failure_count,
            bearish_success_count=bearish_success_count,
            bearish_failure_count=bearish_failure_count,
            bearish_total_count=bearish_success_count + bearish_failure_count,
            total_success_count=bullish_success_count + bearish_success_count,
            total_failure_count=bullish_failure_count + bearish_failure_count,
            total_count=len(analysis_executions),
            analysis_executions=analysis_executions,
        )

    return TradingViewAnalyzeResponse(
        analysis_type=payload.analysis_type,
        bullish_success_count=sum(
            result.bullish_success_count for result in asset_results.values()
        ),
        bullish_failure_count=sum(
            result.bullish_failure_count for result in asset_results.values()
        ),
        bullish_total_count=sum(result.bullish_total_count for result in asset_results.values()),
        bearish_success_count=sum(
            result.bearish_success_count for result in asset_results.values()
        ),
        bearish_failure_count=sum(
            result.bearish_failure_count for result in asset_results.values()
        ),
        bearish_total_count=sum(result.bearish_total_count for result in asset_results.values()),
        total_success_count=sum(result.total_success_count for result in asset_results.values()),
        total_failure_count=sum(result.total_failure_count for result in asset_results.values()),
        total_count=sum(result.total_count for result in asset_results.values()),
        assets=asset_results,
    )

@router.post(
    "/analysis-screenshots",
    summary=(
        "Generate setup view screenshots for every analysis execution using "
        "mplfinance python library"
    ),
    response_model=TradingViewAnalyzeResponse,
)
async def generate_screenshots(
    payload: TradingViewAnalyzeResponse,
) -> TradingViewAnalyzeResponse:
    return add_screenshot_urls_to_analysis(payload)
