"""Tests for web data-source management endpoints."""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from divoom_client.core.display_manager import DisplayManager
from divoom_client.web.app import create_app


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "datasources.json"
    config_path.write_text(
        json.dumps(
            {
                "sources": {
                    "stocks": {
                        "type": "stocks",
                        "symbols": ["^GSPC"],
                        "enabled": False,
                        "refresh_seconds": 300,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = DisplayManager(config_dir=config_dir, assets_dir=tmp_path / "assets")
    manager.load_datasources(config_path)
    return TestClient(create_app(manager)), config_path


def test_datasource_config_can_be_edited_without_fetching_external_service(tmp_path: Path) -> None:
    client, config_path = make_client(tmp_path)

    response = client.put(
        "/api/datasources/stocks",
        json={
            "config": {
                "type": "stocks",
                "symbols": ["^IXIC", "GC=F"],
                "enabled": False,
                "refresh_seconds": 120,
            }
        },
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["sources"]["stocks"]["symbols"] == ["^IXIC", "GC=F"]
    assert saved["sources"]["stocks"]["refresh_seconds"] == 120


def test_datasource_toggle_syncs_runtime_manager(tmp_path: Path, monkeypatch: Any) -> None:
    async def fake_fetch(self: Any) -> dict[str, Any]:
        return {"^GSPC": {"price": 123.45, "symbol": "^GSPC"}}

    monkeypatch.setattr("divoom_client.datasources.stocks.StockDataSource.fetch", fake_fetch)
    client, config_path = make_client(tmp_path)

    assert client.get("/api/datasources").json() == {}

    response = client.post("/api/datasources/stocks/toggle")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert "stocks" in client.get("/api/datasources").json()
    assert client.get("/api/data").json() == {
        "stocks": {"^GSPC": {"price": 123.45, "symbol": "^GSPC"}}
    }
    saved = json.loads(config_path.read_text())
    assert saved["sources"]["stocks"]["enabled"] is True

    response = client.post("/api/datasources/stocks/toggle")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert client.get("/api/data").json() == {}


def test_weather_source_can_be_created_disabled_with_editable_fields(tmp_path: Path) -> None:
    client, config_path = make_client(tmp_path)

    response = client.post(
        "/api/datasources/weather",
        json={
            "config": {
                "type": "weather",
                "enabled": False,
                "api_key": "${OPENWEATHER_API_KEY}",
                "location": "Hesston,KS,US",
                "units": "imperial",
                "refresh_seconds": 600,
            }
        },
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["sources"]["weather"]["location"] == "Hesston,KS,US"
    assert saved["sources"]["weather"]["api_key"] == "${OPENWEATHER_API_KEY}"


def test_refresh_reports_source_errors_without_http_failure(tmp_path: Path) -> None:
    client, _config_path = make_client(tmp_path)
    client.post(
        "/api/datasources/weather",
        json={
            "config": {
                "type": "weather",
                "enabled": True,
                "api_key": "${MISSING_OPENWEATHER_KEY_FOR_TEST}",
                "location": "Hesston,KS,US",
                "units": "imperial",
                "refresh_seconds": 600,
            }
        },
    )

    response = client.post("/api/refresh", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["errors"] == {"weather": "OpenWeatherMap API key not configured"}
    source_status = client.get("/api/datasources").json()
    assert source_status["weather"]["error"] == "OpenWeatherMap API key not configured"


def test_weather_test_returns_no_request_service_response_without_api_key(tmp_path: Path) -> None:
    client, _config_path = make_client(tmp_path)
    client.post(
        "/api/datasources/weather",
        json={
            "config": {
                "type": "weather",
                "enabled": True,
                "api_key": "${MISSING_OPENWEATHER_KEY_FOR_TEST}",
                "location": "Hesston,KS,US",
                "units": "imperial",
                "refresh_seconds": 600,
            }
        },
    )

    response = client.post("/api/datasources/weather/test")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "OpenWeatherMap API key not configured"
    assert body["service_response"]["request_sent"] is False
    assert "no OpenWeatherMap request was sent" in body["service_response"]["reason"]


def test_weather_test_returns_raw_openweather_response(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeResponse:
        status_code = 401
        ok = False
        text = '{"cod":401,"message":"Invalid API key. Please see docs."}'
        url = "https://api.openweathermap.org/data/2.5/weather?q=Hesston&appid=BADKEY"

        def json(self) -> dict[str, Any]:
            return {"cod": 401, "message": "Invalid API key. Please see docs."}

    def fake_get(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("divoom_client.datasources.weather.requests.get", fake_get)
    client, _config_path = make_client(tmp_path)
    client.post(
        "/api/datasources/weather",
        json={
            "config": {
                "type": "weather",
                "enabled": True,
                "api_key": "BADKEY",
                "location": "Hesston,KS,US",
                "units": "imperial",
                "refresh_seconds": 600,
            }
        },
    )

    response = client.post("/api/datasources/weather/test")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "OpenWeatherMap HTTP 401" in body["error"]
    service_response = body["service_response"]
    assert service_response["status_code"] == 401
    assert service_response["json"]["message"] == "Invalid API key. Please see docs."
    assert "BADKEY" not in service_response["url"]
    assert "<redacted>" in service_response["url"]


def test_source_refresh_returns_error_payload_instead_of_http_500(tmp_path: Path) -> None:
    client, _config_path = make_client(tmp_path)
    client.post(
        "/api/datasources/weather",
        json={
            "config": {
                "type": "weather",
                "enabled": True,
                "api_key": "${MISSING_OPENWEATHER_KEY_FOR_TEST}",
                "location": "Hesston,KS,US",
                "units": "imperial",
                "refresh_seconds": 600,
            }
        },
    )

    response = client.post("/api/refresh", json={"source": "weather"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["errors"] == {"weather": "OpenWeatherMap API key not configured"}
    assert body["service_response"]["request_sent"] is False
