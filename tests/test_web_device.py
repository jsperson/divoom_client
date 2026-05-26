"""Tests for web device endpoints."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from divoom_client.web.app import create_app


class FakeDevice:
    """Minimal Pixoo stand-in for device endpoint tests."""

    def __init__(self, ip_address: str) -> None:
        self.ip_address = ip_address

    def get_device_info(self) -> dict[str, str]:
        return {"name": "fake-pixoo"}


class FakeManager:
    """Display manager stand-in focused on device connection behavior."""

    def __init__(self, config_dir: Path, should_connect: bool = True) -> None:
        self.config_dir = config_dir
        self.should_connect = should_connect
        self.connect_calls: list[str | None] = []
        self._device: FakeDevice | None = None

    @property
    def device(self) -> FakeDevice | None:
        return self._device

    def connect(self, ip: str | None = None) -> bool:
        self.connect_calls.append(ip)
        if not self.should_connect:
            self._device = None
            return False
        self._device = FakeDevice(ip or "192.168.3.128")
        return True

    def get_status(self) -> dict[str, Any]:
        return {
            "device_connected": self._device is not None,
            "device_ip": self._device.ip_address if self._device else None,
            "layout_loaded": True,
            "layout_name": "Base",
            "data_sources": [],
            "scheduler_running": False,
            "scheduled_jobs": [],
        }


def test_reconnect_uses_display_manager_connect_without_private_device_ip(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path / "config")
    client = TestClient(create_app(manager))

    response = client.post("/api/device/reconnect")

    assert response.status_code == 200
    assert response.json() == {"success": True, "ip": "192.168.3.128"}
    assert manager.connect_calls == [None]


def test_reconnect_reports_unavailable_device_as_client_error(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path / "config", should_connect=False)
    client = TestClient(create_app(manager))

    response = client.post("/api/device/reconnect")

    assert response.status_code == 400
    assert response.json()["detail"] == "Could not reconnect to device"


def test_connect_to_specific_device_reports_failed_connection(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path / "config", should_connect=False)
    client = TestClient(create_app(manager))

    response = client.post("/api/device/connect/192.168.3.128")

    assert response.status_code == 400
    assert response.json()["detail"] == "Could not connect to device at 192.168.3.128"
