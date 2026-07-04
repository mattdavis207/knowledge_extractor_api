from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.tradingview.constants import (
    AnalysisDirection,
    AnalysisType,
    BiasType,
    CurrencyPairAssetClass,
    PriceCandleType,
)


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


class TradingViewExchangesResponse(BaseModel):
    count: int
    exchanges: list[Any]


class TradingViewPriceCandle(BaseModel):
    time: int
    open: float
    close: float
    max: float
    min: float
    volume: float | int | None = None
    high_before: bool | None = None


AnalysisCaseResult = tuple[bool | None, TradingViewPriceCandle | None, bool]


class TradingViewAssetRequest(BaseModel):
    exchange: str
    bias: BiasType = "Both"


class TradingViewTimeframeRequest(BaseModel):
    label: str | None = None
    query_param: str


class TradingViewPriceDataRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assets: dict[str, TradingViewAssetRequest]
    timeframe: str | TradingViewTimeframeRequest
    start_date: str | None = None
    end_date: str | None = None
    sd: str | None = None
    ed: str | None = None
    analysis_type: AnalysisType | None = None
    candle_type: PriceCandleType = Field(default="Japanese", alias="type")
    include_hourly_candles: bool = False
    request_delay_seconds: float | None = Field(default=None, ge=0)


class ParsedTradingViewAsset(BaseModel):
    pair: str
    exchange: str
    bias: BiasType
    symbol: str
    response_key: str


class TradingViewPriceData(BaseModel):
    symbol: str
    pair: str | None = None
    exchange: str | None = None
    bias: BiasType = "Both"
    timeframe: str
    candle_type: PriceCandleType
    start_date: str
    end_date: str
    range: int
    to: int
    count: int
    history: list[TradingViewPriceCandle]
    context_history: list[TradingViewPriceCandle] = Field(default_factory=list)
    hourly_range: int | None = None
    hourly_to: int | None = None
    hourly_count: int = 0
    hourly_candles: list[TradingViewPriceCandle] = Field(default_factory=list)


PriceDataInput = (
    dict[str, TradingViewPriceData]
    | list[TradingViewPriceData]
    | list[dict[str, TradingViewPriceData]]
)


class TradingViewPriceDataResponse(BaseModel):
    price_data: list[dict[str, TradingViewPriceData]]


class TradingViewAnalysisExecution(BaseModel):
    pair: str
    exchange: str | None = None
    symbol: str | None = None
    bias: BiasType
    analysis_type: AnalysisType
    direction: AnalysisDirection
    success: bool
    consolidation: bool
    inside_bar: bool = False
    prev: TradingViewPriceCandle
    curr: TradingViewPriceCandle
    next_candle: TradingViewPriceCandle
    prev_date: date
    curr_date: date
    next_date: date
    screenshot_url: str | None = None
    screenshot_error: str | None = None


class TradingViewAnalyzeRequest(BaseModel):
    analysis_type: AnalysisType
    histories: dict[str, list[TradingViewPriceCandle]] = Field(default_factory=dict)
    history: dict[str, list[TradingViewPriceCandle]] | None = None
    price_data: PriceDataInput = Field(default_factory=dict)
    assets: dict[str, TradingViewAssetRequest] = Field(default_factory=dict)


class TradingViewAssetAnalyzeResult(BaseModel):
    pair: str
    exchange: str | None = None
    symbol: str | None = None
    bias: BiasType
    analysis_type: AnalysisType
    bullish_success_count: int = 0
    bullish_failure_count: int = 0
    bullish_total_count: int = 0
    bearish_success_count: int = 0
    bearish_failure_count: int = 0
    bearish_total_count: int = 0
    total_success_count: int = 0
    total_failure_count: int = 0
    total_count: int = 0
    context_history: list[TradingViewPriceCandle] = Field(default_factory=list)
    analysis_executions: list[TradingViewAnalysisExecution]


class TradingViewAnalyzeResponse(BaseModel):
    analysis_type: AnalysisType
    bullish_success_count: int = 0
    bullish_failure_count: int = 0
    bullish_total_count: int = 0
    bearish_success_count: int = 0
    bearish_failure_count: int = 0
    bearish_total_count: int = 0
    total_success_count: int = 0
    total_failure_count: int = 0
    total_count: int = 0
    assets: dict[str, TradingViewAssetAnalyzeResult]
