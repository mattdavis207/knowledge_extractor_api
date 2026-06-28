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
    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang):
        assert asset_class == "forex"
        assert tab == "all"
        assert lang == "en"
        return [
            tradingview.TradingViewCurrencyPair(
                symbol="FX:EURUSD",
                exchange="FX",
                ticker="EURUSD",
                label="Euro / U.S. Dollar (FX:EURUSD)",
                description="Euro / U.S. Dollar",
                asset_type="forex",
            )
        ]

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
    async def stub_fetch_currency_pairs_from_leaderboard(*, asset_class, tab, lang):
        assert asset_class == "futures"
        assert tab == "currencies"
        assert lang == "en"
        return [
            tradingview.TradingViewCurrencyPair(
                symbol="CME:6E1!",
                exchange="CME",
                ticker="6E1!",
                label="Euro FX Futures (CME:6E1!)",
                description="Euro FX Futures",
                asset_type="futures",
            )
        ]

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
