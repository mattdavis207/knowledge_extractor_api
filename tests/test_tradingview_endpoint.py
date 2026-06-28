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
                        {"time": 1716768000, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}
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
        params={
            "assets": "FX:EURUSD",
            "timeframe": "W",
            "sd": "2024-01-01",
            "ed": "2024-06-01",
        },
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/api/price/FX%3AEURUSD")
    assert captured["params"] == {
        "timeframe": "W",
        "range": 22,
        "to": 1717286399,
    }
    assert response.json()[0]["symbol"] == "FX:EURUSD"
    assert response.json()[0]["count"] == 1
