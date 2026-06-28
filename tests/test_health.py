from fastapi.testclient import TestClient

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
    assert '"/currency-pairs.json"' in response.text


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
