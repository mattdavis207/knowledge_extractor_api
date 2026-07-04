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
    calculate_lower_timeframe_range_from_timestamps,
    candle_label_date,
    end_date_to_target_timestamp,
    extract_metadata_list,
    filter_candles_to_date_range,
    filter_candles_to_timestamp_range,
    find_high_before_low,  # noqa: F401
    get_candle_time,
    get_context_date_range,
    get_price_data_for_analysis_asset,
    lower_timeframe_for_high_before,
    normalize_price_data_timeframe,
    normalize_tradingview_symbol,
    parse_body_price_assets,
    parse_query_price_assets,
    parse_symbol_parts,
    price_data_to_histories,
    price_data_to_keyed_items,
    start_date_to_target_timestamp,
    timeframe_to_seconds,
    unix_seconds_to_date,
    unix_seconds_to_utc_datetime,  # noqa: F401
)
from app.core.config import settings

router = APIRouter()


currency_pairs_cache: dict[
    CurrencyPairsCacheKey,
    tuple[float, list["TradingViewCurrencyPair"], int | None],
] = {}
RETRYABLE_PRICE_STATUS_CODES = {
    status.HTTP_429_TOO_MANY_REQUESTS,
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    status.HTTP_502_BAD_GATEWAY,
    status.HTTP_503_SERVICE_UNAVAILABLE,
    status.HTTP_504_GATEWAY_TIMEOUT,
}


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


def retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("retry-after")
    if not retry_after:
        return None

    try:
        return max(0.0, float(retry_after))
    except ValueError:
        return None


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


async def fetch_tradingview_price_history_or_raise(
    max_retries: int,
    retry_delay_seconds: float,
    **kwargs,
) -> list[dict]:
    for attempt in range(max_retries + 1):
        try:
            return await fetch_tradingview_price_history(**kwargs)
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in RETRYABLE_PRICE_STATUS_CODES
                or attempt >= max_retries
            ):
                raise_tradingview_http_error(exc)

            wait_seconds = retry_after_seconds(exc.response) or retry_delay_seconds
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="TradingView API request failed.",
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="TradingView API request failed.",
    )


async def fetch_tradingview_price_history_window_or_raise(
    *,
    max_candles_per_request: int,
    request_delay_seconds: float,
    candle_range: int,
    target_timestamp: int,
    timeframe: str,
    **kwargs,
) -> list[dict]:
    if candle_range <= max_candles_per_request:
        return await fetch_tradingview_price_history_or_raise(
            candle_range=candle_range,
            target_timestamp=target_timestamp,
            timeframe=timeframe,
            **kwargs,
        )

    timeframe_seconds = timeframe_to_seconds(timeframe)
    if timeframe_seconds is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported TradingView price timeframe: {timeframe}",
        )

    history_by_time: dict[int, dict] = {}
    remaining_range = candle_range
    current_target_timestamp = target_timestamp

    while remaining_range > 0:
        chunk_range = min(remaining_range, max_candles_per_request)
        chunk_history = await fetch_tradingview_price_history_or_raise(
            candle_range=chunk_range,
            target_timestamp=current_target_timestamp,
            timeframe=timeframe,
            **kwargs,
        )

        chunk_times = []
        for candle in chunk_history:
            candle_time = get_candle_time(candle)
            if candle_time is None:
                continue

            history_by_time[candle_time] = candle
            chunk_times.append(candle_time)

        remaining_range -= chunk_range
        if remaining_range <= 0:
            break

        if chunk_times:
            current_target_timestamp = min(chunk_times) - 1
        else:
            current_target_timestamp -= chunk_range * timeframe_seconds

        if request_delay_seconds > 0:
            await asyncio.sleep(request_delay_seconds)

    return [history_by_time[candle_time] for candle_time in sorted(history_by_time)]


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
    pull_high_before: bool,
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
            timeframe,
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

            history = await fetch_tradingview_price_history_window_or_raise(
                max_retries=settings.tradingview_price_request_max_retries,
                retry_delay_seconds=settings.tradingview_price_request_retry_delay_seconds,
                max_candles_per_request=(
                    settings.tradingview_price_request_max_candles_per_request
                ),
                request_delay_seconds=delay_seconds,
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
            history = filter_candles_to_date_range(
                context_history,
                parsed_start_date,
                parsed_end_date,
                timeframe,
            )

            high_before_candles = []
            high_before_to = None
            high_before_range = None
            high_before_timeframe = None

            # Pull lower timeframe candles when high/low sequencing is needed.
            if pull_high_before:
                high_before_timeframe = lower_timeframe_for_high_before(timeframe)
                high_before_to = context_target_timestamp
                parent_candle_times = [
                    candle_time
                    for candle in context_history
                    if (candle_time := get_candle_time(candle)) is not None
                ]
                lower_timeframe_start_timestamp = (
                    min(parent_candle_times) if parent_candle_times else context_start_timestamp
                )
                high_before_range = calculate_lower_timeframe_range_from_timestamps(
                    lower_timeframe_start_timestamp,
                    context_target_timestamp,
                    high_before_timeframe,
                )
                if upstream_request_count > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

                lower_timeframe_history = await fetch_tradingview_price_history_window_or_raise(
                    max_retries=settings.tradingview_price_request_max_retries,
                    retry_delay_seconds=settings.tradingview_price_request_retry_delay_seconds,
                    max_candles_per_request=(
                        settings.tradingview_price_request_max_candles_per_request
                    ),
                    request_delay_seconds=delay_seconds,
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    asset=asset.symbol,
                    timeframe=high_before_timeframe,
                    candle_type=candle_type,
                    candle_range=high_before_range,
                    target_timestamp=context_target_timestamp,
                )
                upstream_request_count += 1
                high_before_candles = filter_candles_to_timestamp_range(
                    lower_timeframe_history,
                    lower_timeframe_start_timestamp,
                    context_target_timestamp,
                )
                context_history = add_high_low_order_to_parent_candles(
                    parent_candles=context_history,
                    hourly_candles=high_before_candles,
                    timeframe=timeframe,
                    target_timestamp=context_target_timestamp,
                )
                history = filter_candles_to_date_range(
                    context_history,
                    parsed_start_date,
                    parsed_end_date,
                    timeframe,
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
                pull_high_before=pull_high_before,
                high_before_timeframe=high_before_timeframe,
                high_before_range=high_before_range,
                high_before_to=high_before_to,
                high_before_count=len(high_before_candles),
                high_before_candles=high_before_candles,
                hourly_range=high_before_range,
                hourly_to=high_before_to,
                hourly_count=len(high_before_candles),
                hourly_candles=high_before_candles,
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
    pull_high_before: Annotated[
        bool,
        Query(
            description=(
                "Also fetch the configured lower timeframe candles so analysis can infer "
                "whether the parent candle high or low formed first."
            )
        ),
    ] = False,
    include_hourly_candles: Annotated[
        bool | None,
        Query(
            description=(
                "Deprecated alias for pull_high_before. Weekly uses 1-hour candles; "
                "daily uses 15-minute candles."
            )
        ),
    ] = None,
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
        pull_high_before=pull_high_before or bool(include_hourly_candles),
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
        pull_high_before=payload.pull_high_before or bool(payload.include_hourly_candles),
        request_delay_seconds=payload.request_delay_seconds,
    )

    return TradingViewPriceDataResponse(price_data=price_data_to_keyed_items(price_data))


def is_bullish_potential_setup_tm(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> tuple[
    bool,
    TradingViewPriceCandle | None,
    TradingViewPriceCandle | None,
    bool,
]:
    # Candle 2 runs candle 1's low, closes back inside range, and does not run the high.
    is_potential_setup = (
        (curr.min < prev.min) and (prev.min < curr.close <= prev.max) and (curr.max <= prev.max)
    )

    if is_potential_setup:
        return True, None, None, False

    if sorted_history is None:
        return False, None, None, False

    start_index = i
    if start_index is None:
        start_index = next(
            (index for index, candle in enumerate(sorted_history) if candle.time == curr.time),
            None,
        )

    if start_index is None:
        return False, None, None, False

    for j in range(start_index, len(sorted_history) - 1):
        candidate = sorted_history[j]
        candidate_is_potential_setup = (
            (candidate.min < prev.min)
            and (prev.min < candidate.close <= prev.max)
            and (candidate.max <= prev.max)
        )

        if candidate_is_potential_setup:
            return True, candidate, sorted_history[j + 1], True

        if candidate.max > prev.max or candidate.min < prev.min:
            return False, None, None, True

    return False, None, None, True


def is_bearish_potential_setup_tm(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> tuple[
    bool,
    TradingViewPriceCandle | None,
    TradingViewPriceCandle | None,
    bool,
]:  
    # Candle 2 runs candle 1's high, closes back inside range, and does not run the low.
    is_potential_setup = (
        (curr.max > prev.max) and (prev.min <= curr.close < prev.max) and (curr.min >= prev.min)
    )

    if is_potential_setup:
        return True, None, None, False

    if sorted_history is None:
        return False, None, None, False

    start_index = i
    if start_index is None:
        start_index = next(
            (index for index, candle in enumerate(sorted_history) if candle.time == curr.time),
            None,
        )

    if start_index is None:
        return False, None, None, False

    for j in range(start_index, len(sorted_history) - 1):
        candidate = sorted_history[j]
        candidate_is_potential_setup = (
            (candidate.max > prev.max)
            and (prev.min <= candidate.close < prev.max)
            and (candidate.min >= prev.min)
        )

        if candidate_is_potential_setup:
            return True, candidate, sorted_history[j + 1], True

        if candidate.min < prev.min or candidate.max > prev.max:
            return False, None, None, True

    return False, None, None, True



def evaluate_bullish_triple_m_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> tuple[
    bool | None,
    TradingViewPriceCandle | None,
    TradingViewPriceCandle | None,
    bool,
    bool,
]:
    is_potential_setup, new_curr, new_next_candle, inside_bar = is_bullish_potential_setup_tm(
        prev, curr, sorted_history, i
    )

    if not is_potential_setup:
        return None, None, None, False, inside_bar

    replacement_curr = None
    replacement_next = None
    if new_curr is not None:
        replacement_curr = new_curr
        curr = new_curr
        if sorted_history is not None:
            i = next(
                (index for index, candle in enumerate(sorted_history) if candle.time == curr.time),
                i,
            )
    if new_next_candle is not None:
        replacement_next = new_next_candle
        next_candle = new_next_candle
    
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
        return True, replacement_curr, replacement_next, False, inside_bar
    # candle 3 hit the target and did not hit low of candle 2 (even though low came first)
    if next_candle.max > target and next_candle.min >= curr.min:
        return True, replacement_curr, replacement_next, False, inside_bar
     # candle 3 ran the second candle low before validation, so the setup failed immediately.
    if next_candle.min < curr.min:
        return False, replacement_curr, replacement_next, False, inside_bar
    
    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, replacement_curr, replacement_next, False, inside_bar
    
    # Third candle did not resolve the setup, so scan subsequent candles until consolidation breaks.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle high hit the target and high came first.
        if cons_candle.max > target and cons_candle.high_before is True:
            return True, replacement_curr, cons_candle, True, inside_bar
        # Cons candle hit the target and did not hit low of candle 2 (even though low came first)
        if cons_candle.max > target and cons_candle.min >= curr.min:
            return True, replacement_curr, cons_candle, True, inside_bar
        # Cons candle failed the setup
        if cons_candle.min < curr.min:
            return False, replacement_curr, cons_candle, True, inside_bar

        j += 1

    return False, replacement_curr, replacement_next, True, inside_bar



def evaluate_bearish_triple_m_case(
    prev: TradingViewPriceCandle,
    curr: TradingViewPriceCandle,
    next_candle: TradingViewPriceCandle,
    sorted_history: list[TradingViewPriceCandle] | None = None,
    i: int | None = None,
) -> tuple[
    bool | None,
    TradingViewPriceCandle | None,
    TradingViewPriceCandle | None,
    bool,
    bool,
]:
    is_potential_setup, new_curr, new_next_candle, inside_bar = is_bearish_potential_setup_tm(
        prev, curr, sorted_history, i
    )

    if not is_potential_setup:
        return None, None, None, False, inside_bar

    replacement_curr = None
    replacement_next = None
    if new_curr is not None:
        replacement_curr = new_curr
        curr = new_curr
        if sorted_history is not None:
            i = next(
                (index for index, candle in enumerate(sorted_history) if candle.time == curr.time),
                i,
            )
    if new_next_candle is not None:
        replacement_next = new_next_candle
        next_candle = new_next_candle

    target = prev.min + ((prev.max - prev.min) / 2)
    
    # Determine target

    # Scenario 1: candle 2 did not hit %50 of candle 1, target is still %50 of candle 1 
    if curr.min >= target:
        pass
    
    # Scenario 2: candle 2 already hit %50 of candle 1, target is low of candle 2
    elif curr.min < target: 
        target = curr.min
    

    # candle 3 hit the target and low came first.
    if next_candle.min < target and next_candle.high_before is False:
        return True, replacement_curr, replacement_next, False, inside_bar
    # candle 3 hit the target and did not hit high of candle 2 (even though high came first)
    if next_candle.min < target and next_candle.max <= curr.max:
        return True, replacement_curr, replacement_next, False, inside_bar
     # candle 3 ran the second candle high before validation, so the setup failed immediately.
    if next_candle.max > curr.max:
        return False, replacement_curr, replacement_next, False, inside_bar
    
    # Don't check for subsequent consolidation candles without full history context.
    if sorted_history is None or i is None or i == len(sorted_history) - 2:
        return False, replacement_curr, replacement_next, False, inside_bar
    
    # Third candle did not resolve the setup, so scan subsequent candles.
    j = i + 2
    while j < len(sorted_history):
        cons_candle = sorted_history[j]

        # Cons candle hit the target and low came first.
        if cons_candle.min < target and cons_candle.high_before is False:
            return True, replacement_curr, cons_candle, True, inside_bar
        # Cons candle hit the target and did not hit high of candle 2 (even though high came first)
        if cons_candle.min < target and cons_candle.max <= curr.max:
            return True, replacement_curr, cons_candle, True, inside_bar
        # Cons candle failed the setup
        if cons_candle.max > curr.max:
            return False, replacement_curr, cons_candle, True, inside_bar

        j += 1

    return False, replacement_curr, replacement_next, True, inside_bar


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
) -> tuple[str, str | None, str | None, str | None, BiasType]:
    exchange_from_key, pair = parse_symbol_parts(pair_key)
    asset_config = assets.get(pair_key) or assets.get(pair)
    asset_price_data = get_price_data_for_analysis_asset(pair_key, pair, price_data)
    timeframe = asset_price_data.timeframe if asset_price_data else None

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

    return pair, exchange, symbol, timeframe, bias


def infer_candle_interval_seconds(candles: list[TradingViewPriceCandle]) -> int | None:
    candle_times = sorted({candle.time for candle in candles})
    intervals = [
        current_time - previous_time
        for previous_time, current_time in zip(candle_times, candle_times[1:], strict=False)
        if current_time > previous_time
    ]
    return min(intervals) if intervals else None


def screenshot_padding_unit_seconds(
    timeframe: str | None,
    context_history: list[TradingViewPriceCandle],
) -> int:
    if timeframe:
        timeframe_seconds = timeframe_to_seconds(timeframe)
        if timeframe_seconds is not None:
            return timeframe_seconds

    inferred_seconds = infer_candle_interval_seconds(context_history)
    if inferred_seconds is not None:
        return inferred_seconds

    return 7 * 24 * 60 * 60


def generate_screenshot_url(
    asset: TradingViewAssetAnalyzeResult,
    execution: TradingViewAnalysisExecution,
) -> str | None:

    # Use all execution candles so delayed inside-bar and consolidation cases
    # highlight the full setup window, not only the original 3-candle indexes.
    setup_start_unix_seconds = min(
        execution.prev.time,
        execution.curr.time,
        execution.next_candle.time,
    )
    setup_end_unix_seconds = max(
        execution.prev.time,
        execution.curr.time,
        execution.next_candle.time,
    )

    analysis_type = asset.analysis_type
    context_history = sorted(asset.context_history, key=lambda candle: candle.time)
    padding_unit_seconds = screenshot_padding_unit_seconds(
        execution.timeframe or asset.timeframe,
        context_history,
    )

    # calculate padded start_date and end_date
    start_window, end_window = SCREENSHOT_PADDING_WINDOW_WEEKS[analysis_type]
    padded_start_date = setup_start_unix_seconds - (padding_unit_seconds * start_window)
    padded_end_date = setup_end_unix_seconds + (padding_unit_seconds * end_window)
    
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

    # plot and save image to cloudinary
    buffer = plot_to_png_buffer(data, setup_start_unix_seconds, setup_end_unix_seconds)
    public_id = (
        f"{asset.pair}_{execution.direction}_"
        f"{setup_start_unix_seconds}_{execution.curr.time}_{setup_end_unix_seconds}"
    )
    
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
        pair, exchange, symbol, timeframe, bias = resolve_analysis_asset(
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
                execution_curr_candle = curr
                execution_next_candle = next_candle
                consolidation = False
                inside_bar = False
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
                    (
                        success,
                        replacement_curr_candle,
                        replacement_next_candle,
                        consolidation,
                        inside_bar,
                    ) = (
                        evaluate_bullish_triple_m_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i,
                        )
                    )
                    if replacement_curr_candle is not None:
                        execution_curr_candle = replacement_curr_candle
                    if replacement_next_candle is not None:
                        execution_next_candle = replacement_next_candle
                # Bearish Triple M Analysis
                elif payload.analysis_type == "Triple M" and direction == "Bearish":
                    (
                        success,
                        replacement_curr_candle,
                        replacement_next_candle,
                        consolidation,
                        inside_bar,
                    ) = (
                        evaluate_bearish_triple_m_case(
                            prev,
                            curr,
                            next_candle,
                            sorted_history,
                            i,
                        )
                    )
                    if replacement_curr_candle is not None:
                        execution_curr_candle = replacement_curr_candle
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
                        timeframe=timeframe,
                        bias=bias,
                        analysis_type=payload.analysis_type,
                        direction=direction,
                        success=success,
                        consolidation=consolidation,
                        inside_bar=inside_bar,
                        prev=prev,
                        curr=execution_curr_candle,
                        next_candle=execution_next_candle,
                        prev_date=candle_label_date(prev.time, timeframe),
                        curr_date=candle_label_date(execution_curr_candle.time, timeframe),
                        next_date=candle_label_date(execution_next_candle.time, timeframe),
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
            timeframe=timeframe,
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
