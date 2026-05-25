"""Tests for web layout persistence endpoints."""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from divoom_client.web.app import create_app, get_index_html


class FakeLayout:
    """Small layout stand-in with the Pydantic method used by the app."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.name = str(data.get("name", "Test"))

    def model_dump(self) -> dict[str, Any]:
        return dict(self.data)


class FakeManager:
    """Display manager stand-in for layout endpoint tests."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.layout = FakeLayout({"name": "Base", "widgets": [], "refresh_seconds": 60})
        self.data: dict[str, Any] = {}
        self.loaded_paths: list[Path] = []

    def get_status(self) -> dict[str, Any]:
        return {
            "device_connected": False,
            "device_ip": None,
            "layout_loaded": True,
            "layout_name": self.layout.name,
            "data_sources": [],
            "scheduler_running": False,
            "scheduled_jobs": [],
        }

    def load_layout(self, path: Path) -> bool:
        self.loaded_paths.append(path)
        with open(path) as f:
            self.layout = FakeLayout(json.load(f))
        return True

    def _render_and_send(self) -> None:
        return None


def make_client(tmp_path: Path) -> tuple[TestClient, FakeManager]:
    config_dir = tmp_path / "config"
    layouts_dir = config_dir / "layouts"
    layouts_dir.mkdir(parents=True)
    (layouts_dir / "Base.json").write_text(
        json.dumps({"name": "Base", "widgets": [{"type": "text", "text": "Hi"}]}),
        encoding="utf-8",
    )
    manager = FakeManager(config_dir)
    return TestClient(create_app(manager)), manager


def test_index_html_is_loaded_from_static_file() -> None:
    html = get_index_html()
    assert "Divoom Studio" in html
    assert "Design + Preview" in html
    assert "Save / Load" in html


def test_layout_save_duplicate_rename_delete_round_trip(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    layout = {"name": "Ignored", "widgets": [{"type": "text", "text": "Saved"}]}
    response = client.post("/api/layouts/My Layout", json={"layout": layout})
    assert response.status_code == 200

    response = client.get("/api/layouts/My Layout")
    assert response.status_code == 200
    saved = response.json()
    assert saved["name"] == "My Layout"
    assert saved["widgets"][0]["text"] == "Saved"

    response = client.post("/api/layouts/My Layout/duplicate/My Copy")
    assert response.status_code == 200
    assert response.json()["layout"] == "My Copy"

    response = client.post("/api/layouts/My Copy/rename/My Renamed")
    assert response.status_code == 200
    assert response.json()["layout"] == "My Renamed"

    meta = client.get("/api/layouts/meta").json()
    assert {entry["name"] for entry in meta} >= {"Base", "My Layout", "My Renamed"}

    assert client.delete("/api/layouts/My Layout").status_code == 200
    assert client.delete("/api/layouts/My Renamed").status_code == 200
    assert client.get("/api/layouts/My Layout").status_code == 404


def test_layout_names_are_constrained_to_layout_directory(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.post(
        "/api/layouts/../evil",
        json={"layout": {"name": "evil", "widgets": []}},
    )

    assert response.status_code == 404
    assert not (tmp_path / "evil.json").exists()


def test_activating_layout_uses_safe_resolved_file(tmp_path: Path) -> None:
    client, manager = make_client(tmp_path)

    response = client.post("/api/layout/load/Base")

    assert response.status_code == 200
    assert manager.layout.name == "Base"
    assert manager.loaded_paths[-1] == tmp_path / "config" / "layouts" / "Base.json"
