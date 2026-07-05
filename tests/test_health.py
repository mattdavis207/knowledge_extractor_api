from fastapi.testclient import TestClient

import app.main
from app.main import create_app


def test_health_check() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_page_renders_trading_asset_analysis_form() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Trading Asset Analysis" in response.text
    assert "CURRENCY_PAIRS_ENDPOINT" in response.text
    assert "EXCHANGES_ENDPOINT" in response.text
    assert "WEBHOOK_PROXY_ENDPOINT" in response.text
    assert '"/currency-pairs.json"' in response.text
    assert '"/exchanges.json"' in response.text
    assert '"/analysis-webhook"' in response.text


def test_cors_preflight_allows_currency_pairs_endpoint() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/api/v1/tradingview/currency-pairs",
        headers={
            "Origin": "http://localhost:5678",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_local_currency_pairs_json_route() -> None:
    client = TestClient(create_app())

    response = client.get("/currency-pairs.json")

    assert response.status_code == 200
    assert response.json()["count"] == 2512
    assert len(response.json()["currency_pairs"]) == 2512


def test_local_trading_assets_json_route() -> None:
    client = TestClient(create_app())

    response = client.get("/trading-assets.json")

    assert response.status_code == 200
    assert response.json()["count"] == 15
    assert any(asset["ticker"] == "NAS100" for asset in response.json()["assets"])


def test_local_exchanges_json_route() -> None:
    client = TestClient(create_app())

    response = client.get("/exchanges.json")

    assert response.status_code == 200
    assert response.json()["count"] == 352
    assert any(exchange["value"] == "OANDA" for exchange in response.json()["exchanges"])


def test_analysis_webhook_proxy_posts_payload(monkeypatch) -> None:
    async def stub_post_analysis_webhook(payload: dict, webhook_environment: str) -> tuple[int, dict]:
        assert payload == {"assets": ["FX:EURUSD"]}
        assert webhook_environment == "prod"
        return 200, {"execution_url": "https://n8n.example/execution/123"}

    monkeypatch.setattr(app.main, "post_analysis_webhook", stub_post_analysis_webhook)

    client = TestClient(create_app())
    response = client.post("/analysis-webhook", json={"assets": ["FX:EURUSD"]})

    assert response.status_code == 200
    assert response.json() == {
        "status": "sent",
        "webhook_status_code": 200,
        "webhook_response": {"execution_url": "https://n8n.example/execution/123"},
        "execution_url": "https://n8n.example/execution/123",
    }


def test_analysis_webhook_proxy_supports_test_webhook_flag(monkeypatch) -> None:
    async def stub_post_analysis_webhook(payload: dict, webhook_environment: str) -> tuple[int, dict]:
        assert payload == {"assets": ["FX:EURUSD"]}
        assert webhook_environment == "test"
        return 200, {}

    monkeypatch.setattr(app.main, "post_analysis_webhook", stub_post_analysis_webhook)

    client = TestClient(create_app())
    response = client.post(
        "/analysis-webhook?webhook_environment=test",
        json={"assets": ["FX:EURUSD"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "sent",
        "webhook_status_code": 200,
        "webhook_response": {},
    }
