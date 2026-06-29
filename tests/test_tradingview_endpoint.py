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
        "range": 22,
        "to": 1717286399,
    }
    assert response.json()["FX_IDC:CHFJPY"]["symbol"] == "FX_IDC:CHFJPY"
    assert response.json()["FX_IDC:CHFJPY"]["count"] == 1


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
            "range": 2,
            "to": 1705276799,
        },
        {
            "timeframe": "60",
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
