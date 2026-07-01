from datetime import date

import httpx
from fastapi.testclient import TestClient

from app.api.v1.endpoints import tradingview
from app.main import create_app


def test_normalize_tradingview_symbol_uses_existing_exchange_symbol() -> None:
    pair = tradingview.normalize_tradingview_symbol(
        {
            "symbol": "CME:6E1!",
            "exchange": "CME",
            "name": "6E1!",
            "description": "Euro FX Futures",
            "type": "futures",
        }
    )

    assert pair is not None
    assert pair.symbol == "CME:6E1!"
    assert pair.exchange == "CME"
    assert pair.ticker == "6E1!"
    assert pair.label == "Euro FX Futures (CME:6E1!)"


def test_unix_seconds_to_date_uses_utc_date() -> None:
    assert tradingview.unix_seconds_to_date(1704067200) == date(2024, 1, 1)
    assert tradingview.unix_seconds_to_date(1704153599) == date(2024, 1, 1)


def test_metadata_exchanges_endpoint_returns_upstream_exchanges(monkeypatch) -> None:
    captured = {}

    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "exchanges": [
                        {"name": "OANDA", "description": "OANDA"},
                        {"name": "FXCM", "description": "FXCM"},
                    ],
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict) -> StubResponse:
            captured["url"] = url
            captured["headers"] = headers
            return StubResponse()

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/metadata/exchanges")

    assert response.status_code == 200
    assert captured["url"].endswith("/api/metadata/exchanges")
    assert captured["headers"] == {"x-test": "ok"}
    assert response.json() == {
        "count": 2,
        "exchanges": [
            {"name": "OANDA", "description": "OANDA"},
            {"name": "FXCM", "description": "FXCM"},
        ],
    }


def test_metadata_endpoint_alias_returns_exchanges(monkeypatch) -> None:
    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": [
                    {"name": "OANDA", "description": "OANDA"},
                ],
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict) -> StubResponse:
            return StubResponse()

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/metadata")

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "exchanges": [{"name": "OANDA", "description": "OANDA"}],
    }


def test_exchanges_endpoint_alias_returns_exchanges(monkeypatch) -> None:
    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"success": True, "exchanges": [{"name": "OANDA"}]}

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict) -> StubResponse:
            return StubResponse()

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/exchanges")

    assert response.status_code == 200
    assert response.json() == {"count": 1, "exchanges": [{"name": "OANDA"}]}


def test_list_currency_pairs_endpoint_defaults_to_forex(monkeypatch) -> None:
    tradingview.currency_pairs_cache.clear()

    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang, start, count):
        assert asset_class == "forex"
        assert tab == "all"
        assert lang == "en"
        assert start == 0
        assert count == 150
        return (
            [
                tradingview.TradingViewCurrencyPair(
                    symbol="FX:EURUSD",
                    exchange="FX",
                    ticker="EURUSD",
                    label="Euro / U.S. Dollar (FX:EURUSD)",
                    description="Euro / U.S. Dollar",
                    asset_type="forex",
                )
            ],
            1,
        )

    monkeypatch.setattr(
        tradingview,
        "fetch_currency_pairs_from_leaderboard",
        stub_fetch_currency_pairs_from_leaderboard,
    )

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/currency-pairs")

    assert response.status_code == 200
    assert response.json() == {
        "asset_class": "forex",
        "tab": "all",
        "count": 1,
        "cached": False,
        "source": "tradingview",
        "all_requested": False,
        "pages_fetched": 1,
        "start": 0,
        "limit": 150,
        "total_count": 1,
        "symbols": ["FX:EURUSD"],
        "currency_pairs": [
            {
                "symbol": "FX:EURUSD",
                "exchange": "FX",
                "ticker": "EURUSD",
                "label": "Euro / U.S. Dollar (FX:EURUSD)",
                "description": "Euro / U.S. Dollar",
                "asset_type": "forex",
            }
        ],
    }


def test_list_currency_pairs_endpoint_uses_currency_futures_tab(monkeypatch) -> None:
    tradingview.currency_pairs_cache.clear()

    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang, start, count):
        assert asset_class == "futures"
        assert tab == "currencies"
        assert lang == "en"
        return (
            [
                tradingview.TradingViewCurrencyPair(
                    symbol="CME:6E1!",
                    exchange="CME",
                    ticker="6E1!",
                    label="Euro FX Futures (CME:6E1!)",
                    description="Euro FX Futures",
                    asset_type="futures",
                )
            ],
            1,
        )

    monkeypatch.setattr(
        tradingview,
        "fetch_currency_pairs_from_leaderboard",
        stub_fetch_currency_pairs_from_leaderboard,
    )

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/currency-pairs?asset_class=futures")

    assert response.status_code == 200
    assert response.json()["symbols"] == ["CME:6E1!"]
    assert response.json()["tab"] == "currencies"


def test_list_currency_pairs_rejects_invalid_tab() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/tradingview/currency-pairs?asset_class=futures&tab=major")

    assert response.status_code == 400
    assert "Invalid tab" in response.json()["detail"]


def test_list_currency_pairs_uses_cache(monkeypatch) -> None:
    tradingview.currency_pairs_cache.clear()
    calls = 0

    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang, start, count):
        nonlocal calls
        calls += 1
        return (
            [
                tradingview.TradingViewCurrencyPair(
                    symbol="FX:EURUSD",
                    exchange="FX",
                    ticker="EURUSD",
                    label="Euro / U.S. Dollar (FX:EURUSD)",
                )
            ],
            1,
        )

    monkeypatch.setattr(
        tradingview,
        "fetch_currency_pairs_from_leaderboard",
        stub_fetch_currency_pairs_from_leaderboard,
    )

    client = TestClient(create_app())

    first_response = client.get("/api/v1/tradingview/currency-pairs")
    second_response = client.get("/api/v1/tradingview/currency-pairs")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["cached"] is False
    assert second_response.json()["cached"] is True
    assert second_response.headers["x-cache"] == "HIT"
    assert calls == 1


def test_list_currency_pairs_can_fetch_all_pages(monkeypatch) -> None:
    tradingview.currency_pairs_cache.clear()
    requested_starts = []

    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang, start, count):
        requested_starts.append(start)
        page_size = 150 if start == 0 else 100
        return (
            [
                tradingview.TradingViewCurrencyPair(
                    symbol=f"FX:PAIR{start + index}",
                    exchange="FX",
                    ticker=f"PAIR{start + index}",
                    label=f"Pair {start + index} (FX:PAIR{start + index})",
                )
                for index in range(page_size)
            ],
            250,
        )

    monkeypatch.setattr(
        tradingview,
        "fetch_currency_pairs_from_leaderboard",
        stub_fetch_currency_pairs_from_leaderboard,
    )

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/tradingview/currency-pairs?all=true&page_delay_seconds=0"
    )

    assert response.status_code == 200
    assert requested_starts == [0, 150]
    assert response.json()["all_requested"] is True
    assert response.json()["pages_fetched"] == 2
    assert response.json()["count"] == 250
    assert response.json()["total_count"] == 250


def test_list_currency_pairs_returns_fallback_for_upstream_rate_limit(monkeypatch) -> None:
    tradingview.currency_pairs_cache.clear()

    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang, start, count):
        request = httpx.Request("GET", "https://example.test")
        response = httpx.Response(
            status_code=429,
            request=request,
            headers={"Retry-After": "60"},
        )
        raise httpx.HTTPStatusError(
            "Too Many Requests",
            request=request,
            response=response,
        )

    monkeypatch.setattr(
        tradingview,
        "fetch_currency_pairs_from_leaderboard",
        stub_fetch_currency_pairs_from_leaderboard,
    )

    client = TestClient(create_app())
    response = client.get("/api/v1/tradingview/currency-pairs")

    assert response.status_code == 200
    assert response.headers["x-tradingview-upstream-status"] == "429"
    assert response.json()["source"] == "fallback"
    assert "FX:EURUSD" in response.json()["symbols"]


def test_price_data_endpoint_accepts_query_params(monkeypatch) -> None:
    captured = {}

    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": [
                        {
                            "time": 1716768000,
                            "open": 1.0,
                            "close": 1.05,
                            "max": 1.1,
                            "min": 0.9,
                            "volume": 1000,
                        }
                    ]
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return StubResponse()

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/tradingview/price-data",
        params=[
            ("assets[0]", "FX_IDC:CHFJPY"),
            ("timeframe", "W"),
            ("start_date", "2024-01-01"),
            ("end_date", "2024-06-01"),
        ],
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/price/FX_IDC%3ACHFJPY")
    assert captured["params"] == {
        "timeframe": "W",
        "type": "Japanese",
        "range": 22,
        "to": 1717286399,
    }
    assert response.json()["FX_IDC:CHFJPY"]["symbol"] == "FX_IDC:CHFJPY"
    assert response.json()["FX_IDC:CHFJPY"]["candle_type"] == "Japanese"
    assert response.json()["FX_IDC:CHFJPY"]["exchange"] == "FX_IDC"
    assert response.json()["FX_IDC:CHFJPY"]["pair"] == "CHFJPY"
    assert response.json()["FX_IDC:CHFJPY"]["bias"] == "Both"
    assert response.json()["FX_IDC:CHFJPY"]["count"] == 1


def test_price_data_endpoint_accepts_asset_exchange_bias_body(monkeypatch) -> None:
    captured = {}

    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": [
                        {
                            "time": 1716768000,
                            "open": 1.0,
                            "close": 1.05,
                            "max": 1.1,
                            "min": 0.9,
                            "volume": 1000,
                        }
                    ]
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return StubResponse()

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/tradingview/price-data",
        json={
            "assets": {
                "CHFJPY": {
                    "exchange": "FXCM",
                    "bias": "Bullish",
                }
            },
            "timeframe": {
                "label": "Weekly",
                "query_param": "W",
            },
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
        },
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/price/FXCM%3ACHFJPY")
    assert captured["params"] == {
        "timeframe": "W",
        "type": "Japanese",
        "range": 22,
        "to": 1717286399,
    }
    assert response.json() == {
        "price_data": [
            {
                "CHFJPY": {
                    "symbol": "FXCM:CHFJPY",
                    "pair": "CHFJPY",
                    "exchange": "FXCM",
                    "bias": "Bullish",
                    "timeframe": "W",
                    "candle_type": "Japanese",
                    "start_date": "2024-01-01",
                    "end_date": "2024-06-01",
                    "range": 22,
                    "to": 1717286399,
                    "count": 1,
                    "history": [
                        {
                            "time": 1716768000,
                            "open": 1.0,
                            "close": 1.05,
                            "max": 1.1,
                            "min": 0.9,
                            "volume": 1000,
                            "high_before": None,
                        }
                    ],
                    "hourly_range": None,
                    "hourly_to": None,
                    "hourly_count": 0,
                    "hourly_candles": [],
                }
            }
        ]
    }


def test_price_data_endpoint_delays_between_upstream_requests(monkeypatch) -> None:
    captured_urls = []
    sleep_calls = []

    class StubResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": [
                        {
                            "time": 1716768000,
                            "open": 1.0,
                            "close": 1.05,
                            "max": 1.1,
                            "min": 0.9,
                        }
                    ]
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            captured_urls.append(url)
            return StubResponse()

    async def fake_sleep(delay_seconds: float) -> None:
        sleep_calls.append(delay_seconds)

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)
    monkeypatch.setattr(tradingview.asyncio, "sleep", fake_sleep)

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/tradingview/price-data",
        json={
            "assets": {
                "CHFJPY": {"exchange": "OANDA", "bias": "Bullish"},
                "EURUSD": {"exchange": "OANDA", "bias": "Bearish"},
            },
            "timeframe": "W",
            "start_date": "2024-01-01",
            "end_date": "2024-01-14",
            "request_delay_seconds": 0.25,
        },
    )

    assert response.status_code == 200
    assert len(captured_urls) == 2
    assert sleep_calls == [0.25]


def test_price_data_endpoint_can_include_hourly_candles(monkeypatch) -> None:
    captured_params = []

    class StubResponse:
        def __init__(self, history: list[dict]) -> None:
            self.history = history

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": self.history,
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            captured_params.append(params.copy())
            if params["timeframe"] == "60":
                return StubResponse(
                    [
                        {
                            "time": 1704067200,
                            "open": 1.0,
                            "close": 1.01,
                            "max": 1.02,
                            "min": 0.99,
                            "volume": 100,
                        },
                        {
                            "time": 1704153600,
                            "open": 1.01,
                            "close": 1.03,
                            "max": 1.04,
                            "min": 1.0,
                            "volume": 120,
                        },
                    ]
                )

            return StubResponse(
                [
                    {
                        "time": 1704067200,
                        "open": 1.0,
                        "close": 1.03,
                        "max": 1.04,
                        "min": 0.99,
                        "volume": 220,
                    }
                ]
            )

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/tradingview/price-data",
        params=[
            ("assets", "FX_IDC:CHFJPY"),
            ("timeframe", "W"),
            ("sd", "2024-01-01"),
            ("ed", "2024-01-14"),
            ("include_hourly_candles", "true"),
        ],
    )

    assert response.status_code == 200
    assert captured_params == [
        {
            "timeframe": "W",
            "type": "Japanese",
            "range": 2,
            "to": 1705276799,
        },
        {
            "timeframe": "60",
            "type": "Japanese",
            "range": 336,
            "to": 1705276799,
        },
    ]

    data = response.json()["FX_IDC:CHFJPY"]
    assert data["count"] == 1
    assert data["hourly_range"] == 336
    assert data["hourly_to"] == 1705276799
    assert data["hourly_count"] == 2
    assert data["hourly_candles"][0]["time"] == 1704067200
    assert data["history"][0]["high_before"] is False


def test_price_data_endpoint_fetches_hourly_from_first_parent_candle(monkeypatch) -> None:
    captured_params = []

    class StubResponse:
        def __init__(self, history: list[dict]) -> None:
            self.history = history

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": self.history,
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            captured_params.append(params.copy())
            if params["timeframe"] == "60":
                return StubResponse(
                    [
                        {"time": 1703462400, "open": 1.0, "close": 0.95, "max": 1.01, "min": 0.9},
                        {"time": 1703548800, "open": 0.95, "close": 1.05, "max": 1.1, "min": 0.94},
                    ]
                )

            return StubResponse(
                [
                    {
                        "time": 1703462400,
                        "open": 1.0,
                        "close": 1.05,
                        "max": 1.1,
                        "min": 0.9,
                        "volume": 200,
                    }
                ]
            )

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/tradingview/price-data",
        params=[
            ("assets", "FX_IDC:CHFJPY"),
            ("timeframe", "W"),
            ("sd", "2024-01-01"),
            ("ed", "2024-01-14"),
            ("include_hourly_candles", "true"),
        ],
    )

    assert response.status_code == 200
    assert captured_params[1] == {
        "timeframe": "60",
        "type": "Japanese",
        "range": 504,
        "to": 1705276799,
    }
    assert response.json()["FX_IDC:CHFJPY"]["history"][0]["high_before"] is False


def test_price_data_endpoint_keeps_unknown_high_before_field(monkeypatch) -> None:
    class StubResponse:
        def __init__(self, history: list[dict]) -> None:
            self.history = history

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "history": self.history,
                },
            }

    class StubAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict, params: dict) -> StubResponse:
            if params["timeframe"] == "60":
                return StubResponse(
                    [
                        {
                            "time": 1704067200,
                            "open": 1.0,
                            "close": 1.01,
                            "max": 1.02,
                            "min": 0.99,
                            "volume": 100,
                        }
                    ]
                )

            return StubResponse(
                [
                    {
                        "time": 1704067200,
                        "open": 1.0,
                        "close": 1.03,
                        "max": 1.04,
                        "min": 0.98,
                        "volume": 220,
                    }
                ]
            )

    monkeypatch.setattr(tradingview, "get_tradingview_headers", lambda: {"x-test": "ok"})
    monkeypatch.setattr(tradingview.httpx, "AsyncClient", StubAsyncClient)

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/tradingview/price-data",
        params=[
            ("assets", "FX_IDC:CHFJPY"),
            ("timeframe", "W"),
            ("sd", "2024-01-01"),
            ("ed", "2024-01-14"),
            ("include_hourly_candles", "true"),
        ],
    )

    assert response.status_code == 200
    candle = response.json()["FX_IDC:CHFJPY"]["history"][0]
    assert "high_before" in candle
    assert candle["high_before"] is None


def test_analyze_price_data_returns_case_executions_and_totals() -> None:
    client = TestClient(create_app())
    history = [
        {
            "time": 1704067200,
            "open": 9.0,
            "close": 8.0,
            "max": 10.0,
            "min": 7.0,
            "volume": 100,
        },
        {
            "time": 1704153600,
            "open": 8.0,
            "close": 11.0,
            "max": 12.0,
            "min": 7.5,
            "volume": 110,
        },
        {
            "time": 1704240000,
            "open": 7.0,
            "close": 8.0,
            "max": 13.0,
            "min": 7.0,
            "volume": 120,
            "high_before": True,
        },
        {
            "time": 1704326400,
            "open": 8.0,
            "close": 14.0,
            "max": 15.0,
            "min": 8.5,
            "volume": 130,
        },
        {
            "time": 1704412800,
            "open": 14.0,
            "close": 13.0,
            "max": 14.5,
            "min": 8.0,
            "volume": 140,
            "high_before": False,
        },
    ]

    response = client.post(
        "/api/v1/tradingview/analyze",
        json={
            "analysis_type": "Bullish Engulfing",
            "assets": {"CHFJPY": {"exchange": "OANDA", "bias": "Bullish"}},
            "histories": {"CHFJPY": history},
        },
    )

    assert response.status_code == 200
    data = response.json()
    asset = data["assets"]["CHFJPY"]
    executions = asset["analysis_executions"]

    assert data["analysis_type"] == "Bullish Engulfing"
    assert data["bullish_success_count"] == 1
    assert data["bullish_failure_count"] == 1
    assert data["bullish_total_count"] == 2
    assert data["bearish_success_count"] == 0
    assert data["bearish_failure_count"] == 0
    assert data["total_success_count"] == 1
    assert data["total_failure_count"] == 1
    assert data["total_count"] == 2
    assert asset["pair"] == "CHFJPY"
    assert asset["exchange"] == "OANDA"
    assert asset["symbol"] == "OANDA:CHFJPY"
    assert asset["bias"] == "Bullish"
    assert asset["bullish_success_count"] == 1
    assert asset["bullish_failure_count"] == 1
    assert asset["bullish_total_count"] == 2
    assert asset["bearish_total_count"] == 0
    assert asset["total_count"] == 2
    assert len(executions) == 2
    assert executions[0]["success"] is True
    assert executions[0]["direction"] == "Bullish"
    assert executions[0]["analysis_type"] == "Bullish Engulfing"
    assert executions[0]["prev"] == {**history[0], "high_before": None}
    assert executions[0]["curr"] == {**history[1], "high_before": None}
    assert executions[0]["next_candle"] == history[2]
    assert executions[1]["success"] is False
    assert executions[1]["direction"] == "Bullish"


def test_price_data_to_histories_maps_price_data_output() -> None:
    candle = tradingview.TradingViewPriceCandle(
        time=1704067200,
        open=9.0,
        close=8.0,
        max=10.0,
        min=7.0,
    )
    price_data = [
        {
            "CHFJPY": tradingview.TradingViewPriceData(
                symbol="OANDA:CHFJPY",
                pair="CHFJPY",
                exchange="OANDA",
                bias="Bullish",
                timeframe="W",
                candle_type="Japanese",
                start_date="2024-01-01",
                end_date="2024-01-07",
                range=1,
                to=1704671999,
                count=1,
                history=[candle],
            )
        }
    ]

    assert tradingview.price_data_to_histories(price_data) == {"CHFJPY": [candle]}


def test_analyze_price_data_accepts_price_data_output_as_history_source() -> None:
    client = TestClient(create_app())
    history = [
        {
            "time": 1704067200,
            "open": 9.0,
            "close": 8.0,
            "max": 10.0,
            "min": 7.0,
        },
        {
            "time": 1704153600,
            "open": 8.0,
            "close": 11.0,
            "max": 12.0,
            "min": 7.5,
        },
        {
            "time": 1704240000,
            "open": 7.0,
            "close": 8.0,
            "max": 13.0,
            "min": 7.0,
            "high_before": True,
        },
    ]

    response = client.post(
        "/api/v1/tradingview/analyze",
        json={
            "analysis_type": "Bullish Engulfing",
            "price_data": [
                {
                    "CHFJPY": {
                        "symbol": "OANDA:CHFJPY",
                        "pair": "CHFJPY",
                        "exchange": "OANDA",
                        "bias": "Bullish",
                        "timeframe": "W",
                        "candle_type": "Japanese",
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-21",
                        "range": 3,
                        "to": 1705795199,
                        "count": 3,
                        "history": history,
                    }
                }
            ],
        },
    )

    assert response.status_code == 200
    asset = response.json()["assets"]["CHFJPY"]
    assert asset["exchange"] == "OANDA"
    assert asset["bias"] == "Bullish"
    assert asset["bullish_success_count"] == 1
    assert asset["total_count"] == 1


def test_analyze_price_data_respects_bearish_bias() -> None:
    client = TestClient(create_app())
    history = [
        {
            "time": 1704067200,
            "open": 10.0,
            "close": 9.5,
            "max": 11.0,
            "min": 9.0,
        },
        {
            "time": 1704153600,
            "open": 9.5,
            "close": 8.5,
            "max": 10.0,
            "min": 8.0,
        },
        {
            "time": 1704240000,
            "open": 8.5,
            "close": 9.0,
            "max": 10.5,
            "min": 7.5,
            "high_before": False,
        },
    ]

    response = client.post(
        "/api/v1/tradingview/analyze",
        json={
            "analysis_type": "Bullish Engulfing",
            "assets": {"EURUSD": {"exchange": "OANDA", "bias": "Bearish"}},
            "histories": {"EURUSD": history},
        },
    )

    assert response.status_code == 200
    data = response.json()
    asset = data["assets"]["EURUSD"]

    assert data["bullish_total_count"] == 0
    assert data["bearish_success_count"] == 1
    assert data["bearish_failure_count"] == 0
    assert data["total_count"] == 1
    assert asset["bias"] == "Bearish"
    assert asset["bearish_total_count"] == 1
    assert asset["analysis_executions"][0]["direction"] == "Bearish"


def test_analyze_price_data_sorts_history_chronologically() -> None:
    client = TestClient(create_app())
    history = [
        {
            "time": 1704240000,
            "open": 7.0,
            "close": 8.0,
            "max": 13.0,
            "min": 7.0,
            "volume": 120,
            "high_before": True,
        },
        {
            "time": 1704153600,
            "open": 8.0,
            "close": 11.0,
            "max": 12.0,
            "min": 7.5,
            "volume": 110,
        },
        {
            "time": 1704067200,
            "open": 9.0,
            "close": 8.0,
            "max": 10.0,
            "min": 7.0,
            "volume": 100,
        },
    ]

    response = client.post(
        "/api/v1/tradingview/analyze",
        json={
            "analysis_type": "Bullish Engulfing",
            "assets": {"CHFJPY": {"exchange": "OANDA", "bias": "Bullish"}},
            "histories": {"CHFJPY": history},
        },
    )

    assert response.status_code == 200
    execution = response.json()["assets"]["CHFJPY"]["analysis_executions"][0]
    assert execution["prev"]["time"] == 1704067200
    assert execution["curr"]["time"] == 1704153600
    assert execution["next_candle"]["time"] == 1704240000
    assert execution["prev_date"] == "2024-01-01"
    assert execution["curr_date"] == "2024-01-02"
    assert execution["next_date"] == "2024-01-03"
    assert "prev_utc_datetime" not in execution
    assert "prev_ny_datetime" not in execution
    assert execution["success"] is True


def test_bearish_engulfing_requires_known_low_first_when_both_sides_run() -> None:
    prev = tradingview.TradingViewPriceCandle(
        time=1704067200,
        open=10.0,
        close=9.5,
        max=11.0,
        min=9.0,
    )
    curr = tradingview.TradingViewPriceCandle(
        time=1704153600,
        open=9.5,
        close=8.5,
        max=10.0,
        min=8.0,
    )
    next_candle = tradingview.TradingViewPriceCandle(
        time=1704240000,
        open=8.5,
        close=9.0,
        max=10.5,
        min=7.5,
        high_before=None,
    )

    assert tradingview.check_bearish_engulfing(prev, curr, next_candle) is False


def test_bearish_engulfing_accepts_known_low_first_when_both_sides_run() -> None:
    prev = tradingview.TradingViewPriceCandle(
        time=1704067200,
        open=10.0,
        close=9.5,
        max=11.0,
        min=9.0,
    )
    curr = tradingview.TradingViewPriceCandle(
        time=1704153600,
        open=9.5,
        close=8.5,
        max=10.0,
        min=8.0,
    )
    next_candle = tradingview.TradingViewPriceCandle(
        time=1704240000,
        open=8.5,
        close=9.0,
        max=10.5,
        min=7.5,
        high_before=False,
    )

    assert tradingview.check_bearish_engulfing(prev, curr, next_candle) is True


def test_bearish_engulfing_accepts_low_run_without_high_run() -> None:
    prev = tradingview.TradingViewPriceCandle(
        time=1704067200,
        open=10.0,
        close=9.5,
        max=11.0,
        min=9.0,
    )
    curr = tradingview.TradingViewPriceCandle(
        time=1704153600,
        open=9.5,
        close=8.5,
        max=10.0,
        min=8.0,
    )
    next_candle = tradingview.TradingViewPriceCandle(
        time=1704240000,
        open=8.5,
        close=9.0,
        max=9.5,
        min=7.5,
        high_before=None,
    )

    assert tradingview.check_bearish_engulfing(prev, curr, next_candle) is True


def test_engulfing_evaluator_skips_non_potential_setups() -> None:
    prev = tradingview.TradingViewPriceCandle(
        time=1704067200,
        open=9.0,
        close=8.0,
        max=10.0,
        min=7.0,
    )
    curr = tradingview.TradingViewPriceCandle(
        time=1704153600,
        open=8.0,
        close=8.5,
        max=9.0,
        min=6.5,
    )
    next_candle = tradingview.TradingViewPriceCandle(
        time=1704240000,
        open=8.5,
        close=9.0,
        max=10.5,
        min=6.0,
    )

    assert tradingview.evaluate_bullish_engulfing_case(prev, curr, next_candle) is None


def test_high_low_order_handles_newest_first_parent_candles() -> None:
    parent_candles = [
        {
            "time": 1704672000,
            "open": 1.1,
            "close": 1.15,
            "max": 1.2,
            "min": 1.0,
            "volume": 220,
        },
        {
            "time": 1704067200,
            "open": 1.0,
            "close": 1.05,
            "max": 1.1,
            "min": 0.9,
            "volume": 200,
        },
    ]
    hourly_candles = [
        {"time": 1704067200, "open": 1.0, "close": 0.95, "max": 1.02, "min": 0.9},
        {"time": 1704153600, "open": 0.95, "close": 1.05, "max": 1.1, "min": 0.94},
        {"time": 1704672000, "open": 1.1, "close": 1.18, "max": 1.2, "min": 1.08},
        {"time": 1704758400, "open": 1.18, "close": 1.05, "max": 1.19, "min": 1.0},
    ]

    enriched = tradingview.add_high_low_order_to_parent_candles(
        parent_candles=parent_candles,
        hourly_candles=hourly_candles,
        timeframe="W",
        target_timestamp=1705276799,
    )

    assert [candle["time"] for candle in enriched] == [1704672000, 1704067200]
    assert enriched[0]["high_before"] is True
    assert enriched[1]["high_before"] is False


def test_high_low_order_accepts_high_low_field_names() -> None:
    result = tradingview.find_high_before_low(
        {"time": 1704067200, "high": 1.1, "low": 0.9},
        [
            {"time": 1704067200, "high": 1.1, "low": 1.0},
            {"time": 1704153600, "high": 1.05, "low": 0.9},
        ],
    )

    assert result is True


def test_high_low_order_falls_back_to_hourly_extremes_when_parent_prices_do_not_match() -> None:
    result = tradingview.find_high_before_low(
        {"time": 1704067200, "max": 1.11, "min": 0.89},
        [
            {"time": 1704067200, "max": 1.02, "min": 0.9},
            {"time": 1704153600, "max": 1.1, "min": 0.94},
        ],
    )

    assert result is False
