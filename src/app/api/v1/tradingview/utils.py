import math
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time

from app.api.v1.tradingview.constants import (
    ANALYSIS_CONTEXT_WINDOWS_WEEKS,
    PRICE_TIMEFRAME_SECONDS,
    AnalysisType,
)
from app.api.v1.tradingview.schemas import (
    ParsedTradingViewAsset,
    PriceDataInput,
    TradingViewAssetRequest,
    TradingViewCurrencyPair,
    TradingViewPriceCandle,
    TradingViewPriceData,
    TradingViewTimeframeRequest,
)


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


def get_context_date_range(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    analysis_type: AnalysisType | None,
) -> tuple[date, date]:
    parsed_start_date = parse_analysis_date(start_date)
    parsed_end_date = parse_analysis_date(end_date)

    if analysis_type is None:
        return parsed_start_date, parsed_end_date

    start_window_weeks, end_window_weeks = ANALYSIS_CONTEXT_WINDOWS_WEEKS[analysis_type]
    return (
        parsed_start_date - timedelta(weeks=start_window_weeks),
        parsed_end_date + timedelta(weeks=end_window_weeks),
    )


def unix_seconds_to_utc_datetime(timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def unix_seconds_to_date(timestamp: int | float) -> date:
    return unix_seconds_to_utc_datetime(timestamp).date()


def parse_symbol_parts(symbol: str) -> tuple[str | None, str]:
    if ":" not in symbol:
        return None, symbol.strip().upper()

    exchange, pair = symbol.split(":", maxsplit=1)
    return exchange.strip().upper(), pair.strip().upper()


def build_tradingview_symbol(exchange: str, pair: str) -> str:
    return f"{exchange.strip().upper()}:{pair.strip().upper()}"


def parse_query_price_assets(raw_assets: list[str]) -> list[ParsedTradingViewAsset]:
    parsed_assets: list[ParsedTradingViewAsset] = []

    for raw_asset in raw_assets:
        exchange, pair = parse_symbol_parts(raw_asset)
        symbol = build_tradingview_symbol(exchange, pair) if exchange else pair
        parsed_assets.append(
            ParsedTradingViewAsset(
                pair=pair,
                exchange=exchange or "",
                bias="Both",
                symbol=symbol,
                response_key=symbol,
            )
        )

    return parsed_assets


def parse_body_price_assets(
    assets: dict[str, TradingViewAssetRequest],
) -> list[ParsedTradingViewAsset]:
    parsed_assets: list[ParsedTradingViewAsset] = []

    for pair_key, config in assets.items():
        _, pair = parse_symbol_parts(pair_key)
        symbol = build_tradingview_symbol(config.exchange, pair)
        parsed_assets.append(
            ParsedTradingViewAsset(
                pair=pair,
                exchange=config.exchange.strip().upper(),
                bias=config.bias,
                symbol=symbol,
                response_key=pair,
            )
        )

    return parsed_assets


def normalize_price_data_timeframe(timeframe: str | TradingViewTimeframeRequest) -> str:
    if isinstance(timeframe, TradingViewTimeframeRequest):
        return timeframe.query_param

    return timeframe


def get_price_data_asset_key(asset_price_data: TradingViewPriceData) -> str:
    if asset_price_data.pair:
        return asset_price_data.pair

    _, pair = parse_symbol_parts(asset_price_data.symbol)
    return pair


def add_price_data_to_asset_map(
    asset_map: dict[str, TradingViewPriceData],
    asset_key: str,
    asset_price_data: TradingViewPriceData,
) -> None:
    asset_map[asset_key] = asset_price_data
    asset_map.setdefault(get_price_data_asset_key(asset_price_data), asset_price_data)
    asset_map.setdefault(asset_price_data.symbol, asset_price_data)


def price_data_to_keyed_items(
    price_data: dict[str, TradingViewPriceData],
) -> list[dict[str, TradingViewPriceData]]:
    return [{asset_key: asset_price_data} for asset_key, asset_price_data in price_data.items()]


def price_data_to_asset_map(
    price_data: PriceDataInput,
) -> dict[str, TradingViewPriceData]:
    if isinstance(price_data, dict):
        return price_data

    asset_map: dict[str, TradingViewPriceData] = {}

    for item in price_data:
        if isinstance(item, TradingViewPriceData):
            add_price_data_to_asset_map(
                asset_map,
                get_price_data_asset_key(item),
                item,
            )
            continue

        for asset_key, asset_price_data in item.items():
            add_price_data_to_asset_map(asset_map, asset_key, asset_price_data)

    return asset_map


def price_data_to_histories(
    price_data: PriceDataInput,
) -> dict[str, list[TradingViewPriceCandle]]:
    if isinstance(price_data, dict):
        return {
            asset_key: asset_price_data.history
            for asset_key, asset_price_data in price_data.items()
        }

    histories: dict[str, list[TradingViewPriceCandle]] = {}

    for item in price_data:
        if isinstance(item, TradingViewPriceData):
            histories[get_price_data_asset_key(item)] = item.history
            continue

        for asset_key, asset_price_data in item.items():
            histories[asset_key] = asset_price_data.history

    return histories


def get_price_data_for_analysis_asset(
    pair_key: str,
    pair: str,
    price_data: PriceDataInput,
) -> TradingViewPriceData | None:
    asset_map = price_data_to_asset_map(price_data)
    return asset_map.get(pair_key) or asset_map.get(pair)


def start_date_to_target_timestamp(start_date: str | date | datetime) -> int:
    parsed_start_date = parse_analysis_date(start_date)
    start_of_day_utc = datetime.combine(
        parsed_start_date,
        datetime_time(0, 0, 0),
        tzinfo=UTC,
    )
    return int(start_of_day_utc.timestamp())


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


def extract_metadata_list(payload: object, key: str) -> list[object]:
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    value = payload.get(key)
    if isinstance(value, list):
        return value

    data = payload.get("data")
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return value

        nested_data = data.get("data")
        if isinstance(nested_data, list):
            return nested_data

        items = data.get("items")
        if isinstance(items, list):
            return items

    return []


def filter_candles_to_timestamp_range(
    history: list[dict],
    start_timestamp: int,
    end_timestamp: int,
) -> list[dict]:
    filtered_history = []

    for candle in history:
        if not isinstance(candle, dict):
            continue

        candle_time = candle.get("time")
        if not isinstance(candle_time, int):
            continue

        if start_timestamp <= candle_time <= end_timestamp:
            filtered_history.append(candle)

    return filtered_history


def prices_match(
    first_price: float | int | None,
    second_price: float | int | None,
) -> bool:
    if first_price is None or second_price is None:
        return False

    return math.isclose(
        float(first_price),
        float(second_price),
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def calculate_hourly_range_from_timestamps(
    start_timestamp: int,
    end_timestamp: int,
) -> int:
    if end_timestamp < start_timestamp:
        raise ValueError("end_timestamp must be on or after start_timestamp")

    seconds_in_range = end_timestamp - start_timestamp + 1
    return math.ceil(seconds_in_range / PRICE_TIMEFRAME_SECONDS["60"])


def get_price_value(candle: dict, keys: tuple[str, ...]) -> float | int | None:
    for key in keys:
        value = candle.get(key)
        if isinstance(value, int | float):
            return value

    return None


def get_high_price(candle: dict) -> float | int | None:
    return get_price_value(candle, ("max", "high"))


def get_low_price(candle: dict) -> float | int | None:
    return get_price_value(candle, ("min", "low"))


def get_candle_time(candle: dict) -> int | None:
    candle_time = candle.get("time")
    if isinstance(candle_time, int):
        return candle_time

    return None


def timeframe_to_seconds(timeframe: str) -> int | None:
    if timeframe in PRICE_TIMEFRAME_SECONDS:
        return PRICE_TIMEFRAME_SECONDS[timeframe]

    if timeframe == "D":
        return 24 * 60 * 60

    if timeframe == "W":
        return 7 * 24 * 60 * 60

    return None


def find_high_before_low(
    parent_candle: dict,
    child_candles: list[dict],
) -> bool | None:
    high_time = None
    low_time = None
    sorted_child_candles = sorted(
        child_candles,
        key=lambda candle: get_candle_time(candle) or 0,
    )

    for child_candle in sorted_child_candles:
        child_time = get_candle_time(child_candle)
        if child_time is None:
            continue

        if high_time is None and prices_match(
            get_high_price(child_candle),
            get_high_price(parent_candle),
        ):
            high_time = child_time

        if low_time is None and prices_match(
            get_low_price(child_candle),
            get_low_price(parent_candle),
        ):
            low_time = child_time

        if high_time is not None and low_time is not None:
            break

    if high_time is None or low_time is None:
        child_highs = [
            high
            for child_candle in sorted_child_candles
            if (high := get_high_price(child_candle)) is not None
        ]
        child_lows = [
            low
            for child_candle in sorted_child_candles
            if (low := get_low_price(child_candle)) is not None
        ]

        if not child_highs or not child_lows:
            return None

        max_child_high = max(child_highs)
        min_child_low = min(child_lows)

        for child_candle in sorted_child_candles:
            child_time = get_candle_time(child_candle)
            if child_time is None:
                continue

            if high_time is None and prices_match(
                get_high_price(child_candle),
                max_child_high,
            ):
                high_time = child_time

            if low_time is None and prices_match(
                get_low_price(child_candle),
                min_child_low,
            ):
                low_time = child_time

            if high_time is not None and low_time is not None:
                break

    if high_time is None or low_time is None or high_time == low_time:
        return None

    return high_time < low_time


def add_high_low_order_to_parent_candles(
    *,
    parent_candles: list[dict],
    hourly_candles: list[dict],
    timeframe: str,
    target_timestamp: int,
) -> list[dict]:
    timeframe_seconds = timeframe_to_seconds(timeframe)
    sorted_hourly_candles = sorted(
        hourly_candles,
        key=lambda candle: get_candle_time(candle) or 0,
    )
    indexed_parent_candles = list(enumerate(parent_candles))
    sorted_parent_candles = sorted(
        indexed_parent_candles,
        key=lambda item: get_candle_time(item[1]) or 0,
    )
    enriched_by_original_index: dict[int, dict] = {}

    for sorted_index, (original_index, parent_candle) in enumerate(sorted_parent_candles):
        candle_time = get_candle_time(parent_candle)
        enriched_candle = parent_candle.copy()

        if candle_time is None:
            enriched_candle["high_before"] = None
            enriched_by_original_index[original_index] = enriched_candle
            continue

        next_candle_time = None
        if sorted_index + 1 < len(sorted_parent_candles):
            next_candle_time = get_candle_time(
                sorted_parent_candles[sorted_index + 1][1]
            )

        if next_candle_time is not None:
            candle_end_time = min(next_candle_time - 1, target_timestamp)
        elif timeframe_seconds is not None:
            candle_end_time = min(candle_time + timeframe_seconds - 1, target_timestamp)
        else:
            candle_end_time = target_timestamp

        candle_hourly_candles = [
            hourly_candle
            for hourly_candle in sorted_hourly_candles
            if (
                (hourly_candle_time := get_candle_time(hourly_candle)) is not None
                and candle_time <= hourly_candle_time <= candle_end_time
            )
        ]

        enriched_candle["high_before"] = find_high_before_low(
            enriched_candle,
            candle_hourly_candles,
        )
        enriched_by_original_index[original_index] = enriched_candle

    return [
        enriched_by_original_index[index]
        for index in range(len(parent_candles))
        if index in enriched_by_original_index
    ]
