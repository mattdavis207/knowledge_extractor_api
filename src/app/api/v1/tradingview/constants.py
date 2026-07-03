from typing import Literal

CurrencyPairAssetClass = Literal["forex", "futures"]
AnalysisType = Literal["Bullish Engulfing", "Triple M"]
PriceCandleType = Literal["Japanese", "HeikinAshi", "Range"]
BiasType = Literal["Bullish", "Bearish", "Both"]
AnalysisDirection = Literal["Bullish", "Bearish"]

ANALYSIS_CONTEXT_WINDOWS_WEEKS: dict[AnalysisType, tuple[int, int]] = {
    "Bullish Engulfing": (12, 6),
    "Triple M": (0, 0),
}
SCREENSHOT_PADDING_WINDOW_WEEKS: dict[AnalysisType, tuple[int, int]] = {
    "Bullish Engulfing": (10, 5),
    "Triple M": (0, 0),
}

PRICE_ENDPOINT: str = "/api/price"
METADATA_EXCHANGES_ENDPOINT: str = "/api/metadata/exchanges"
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
