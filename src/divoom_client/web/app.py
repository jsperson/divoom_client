"""FastAPI web application for Divoom Client."""

import asyncio
import base64
import io
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from PIL import Image

logger = logging.getLogger(__name__)


# --- Request Models ---

class RefreshRequest(BaseModel):
    """Request to refresh data sources."""
    source: Optional[str] = None


class LayoutUpdate(BaseModel):
    """Request to update layout."""
    layout: dict[str, Any]


class PowerRequest(BaseModel):
    """Power control request."""
    on: bool


class WidgetCreate(BaseModel):
    """Create a new widget."""
    widget: dict[str, Any]


class WidgetUpdate(BaseModel):
    """Update widget properties."""
    updates: dict[str, Any]


class DataSourceCreate(BaseModel):
    """Create a data source."""
    config: dict[str, Any]


class QuickTextRequest(BaseModel):
    """Quick text display request."""
    text: str
    x: int = 0
    y: int = 28
    font: str = "5x7"
    color: str = "#FFFFFF"
    background: str = "#000000"


class NewLayoutRequest(BaseModel):
    """Create new layout request."""
    name: str


def create_app(display_manager: Any) -> FastAPI:
    """Create the FastAPI application.

    Args:
        display_manager: DisplayManager instance

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(
        title="Divoom Client",
        description="Web interface for Divoom Pixoo 64 display manager",
        version="0.1.0",
    )

    app.state.display_manager = display_manager

    # --- Status & Data APIs ---

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        """Get current status of the display manager."""
        return display_manager.get_status()

    @app.get("/api/data")
    async def get_data() -> dict[str, Any]:
        """Get current data from all sources."""
        return display_manager.data

    @app.post("/api/refresh")
    async def refresh_data(request: RefreshRequest) -> dict[str, Any]:
        """Refresh data from sources."""
        try:
            if request.source:
                data = await display_manager._data_manager.refresh(request.source)
                return {"success": True, "source": request.source, "data": data}
            else:
                data = await display_manager._data_manager.refresh_all()
                display_manager._last_data = data
                display_manager._render_and_send()
                return {"success": True, "data": data}
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # --- Layout APIs ---

    def ensure_widget_ids(layout_data: dict) -> dict:
        """Ensure all widgets have unique IDs."""
        for widget in layout_data.get("widgets", []):
            if not widget.get("id"):
                widget["id"] = f"{widget.get('type', 'widget')}_{uuid.uuid4().hex[:8]}"
        return layout_data

    @app.get("/api/layout")
    async def get_layout() -> dict[str, Any]:
        """Get current layout configuration."""
        if not display_manager.layout:
            raise HTTPException(status_code=404, detail="No layout loaded")
        layout_data = display_manager.layout.model_dump()
        ensure_widget_ids(layout_data)
        return layout_data

    @app.get("/api/layouts")
    async def list_layouts() -> list[str]:
        """List available layouts."""
        layouts_dir = display_manager.config_dir / "layouts"
        if not layouts_dir.exists():
            return []
        return sorted([p.stem for p in layouts_dir.glob("*.json")])

    @app.get("/api/layouts/{name}")
    async def get_layout_by_name(name: str) -> dict[str, Any]:
        """Get a specific layout by name."""
        layout_path = display_manager.config_dir / "layouts" / f"{name}.json"
        if not layout_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        with open(layout_path) as f:
            return json.load(f)

    @app.post("/api/layouts/{name}")
    async def save_layout(name: str, update: LayoutUpdate) -> dict[str, Any]:
        """Save a layout."""
        layouts_dir = display_manager.config_dir / "layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        layout_path = layouts_dir / f"{name}.json"
        # Atomic write
        temp_path = layout_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(update.layout, f, indent=2)
        temp_path.rename(layout_path)
        return {"success": True, "path": str(layout_path)}

    @app.delete("/api/layouts/{name}")
    async def delete_layout(name: str) -> dict[str, Any]:
        """Delete a layout."""
        layout_path = display_manager.config_dir / "layouts" / f"{name}.json"
        if not layout_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        layout_path.unlink()
        return {"success": True}

    @app.post("/api/layout/load/{name}")
    async def load_layout(name: str) -> dict[str, Any]:
        """Load and activate a layout."""
        layout_path = display_manager.config_dir / "layouts" / f"{name}.json"
        if not display_manager.load_layout(layout_path):
            raise HTTPException(status_code=400, detail=f"Failed to load layout: {name}")
        display_manager._render_and_send()
        return {"success": True, "layout": name}

    @app.post("/api/layout/new")
    async def create_new_layout(request: NewLayoutRequest) -> dict[str, Any]:
        """Create a new empty layout."""
        layouts_dir = display_manager.config_dir / "layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        layout_path = layouts_dir / f"{request.name}.json"
        if layout_path.exists():
            raise HTTPException(status_code=400, detail=f"Layout already exists: {request.name}")
        new_layout = {
            "name": request.name,
            "background": "#000000",
            "refresh_seconds": 300,
            "widgets": []
        }
        with open(layout_path, "w") as f:
            json.dump(new_layout, f, indent=2)
        return {"success": True, "layout": new_layout}

    # --- Widget APIs ---

    @app.post("/api/layout/widget")
    async def add_widget(request: WidgetCreate) -> dict[str, Any]:
        """Add a widget to the current layout."""
        if not display_manager.layout:
            raise HTTPException(status_code=400, detail="No layout loaded")

        widget = request.widget.copy()
        if "id" not in widget or not widget["id"]:
            widget["id"] = f"{widget.get('type', 'widget')}_{uuid.uuid4().hex[:8]}"

        # Add to layout and ensure all widgets have IDs
        layout_data = display_manager.layout.model_dump()
        ensure_widget_ids(layout_data)
        layout_data["widgets"].append(widget)

        # Save and reload
        layout_path = display_manager.config_dir / "layouts" / f"{display_manager.layout.name}.json"
        with open(layout_path, "w") as f:
            json.dump(layout_data, f, indent=2)
        display_manager.load_layout(layout_path)
        display_manager._render_and_send()

        return {"success": True, "widget_id": widget["id"], "layout": layout_data}

    @app.put("/api/layout/widget/{widget_id}")
    async def update_widget(widget_id: str, request: WidgetUpdate) -> dict[str, Any]:
        """Update a widget in the current layout."""
        if not display_manager.layout:
            raise HTTPException(status_code=400, detail="No layout loaded")

        layout_data = display_manager.layout.model_dump()
        ensure_widget_ids(layout_data)
        widget_found = False

        for widget in layout_data["widgets"]:
            if widget.get("id") == widget_id:
                widget.update(request.updates)
                widget_found = True
                break

        if not widget_found:
            raise HTTPException(status_code=404, detail=f"Widget not found: {widget_id}")

        # Save and reload
        layout_path = display_manager.config_dir / "layouts" / f"{display_manager.layout.name}.json"
        with open(layout_path, "w") as f:
            json.dump(layout_data, f, indent=2)
        display_manager.load_layout(layout_path)
        display_manager._render_and_send()

        return {"success": True, "layout": layout_data}

    @app.delete("/api/layout/widget/{widget_id}")
    async def delete_widget(widget_id: str) -> dict[str, Any]:
        """Delete a widget from the current layout."""
        if not display_manager.layout:
            raise HTTPException(status_code=400, detail="No layout loaded")

        layout_data = display_manager.layout.model_dump()
        ensure_widget_ids(layout_data)
        original_len = len(layout_data["widgets"])
        layout_data["widgets"] = [w for w in layout_data["widgets"] if w.get("id") != widget_id]

        if len(layout_data["widgets"]) == original_len:
            raise HTTPException(status_code=404, detail=f"Widget not found: {widget_id}")

        # Save and reload
        layout_path = display_manager.config_dir / "layouts" / f"{display_manager.layout.name}.json"
        with open(layout_path, "w") as f:
            json.dump(layout_data, f, indent=2)
        display_manager.load_layout(layout_path)
        display_manager._render_and_send()

        return {"success": True, "layout": layout_data}

    # --- Preview APIs ---

    @app.get("/api/preview")
    async def get_preview() -> Response:
        """Get current frame as PNG image."""
        frame = display_manager.render()
        if not frame:
            raise HTTPException(status_code=404, detail="No frame available")
        img = frame.to_image()
        img = img.resize((256, 256), resample=0)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return Response(content=buffer.getvalue(), media_type="image/png")

    @app.get("/api/preview/base64")
    async def get_preview_base64() -> dict[str, str]:
        """Get current frame as base64-encoded PNG."""
        frame = display_manager.render()
        if not frame:
            raise HTTPException(status_code=404, detail="No frame available")
        img = frame.to_image()
        img = img.resize((256, 256), resample=0)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {"image": f"data:image/png;base64,{b64}"}

    @app.post("/api/preview/render")
    async def render_preview(update: LayoutUpdate) -> dict[str, str]:
        """Render a layout preview without saving."""
        from divoom_client.models.layout import Layout
        try:
            layout = Layout.model_validate(update.layout)
            frame = display_manager._renderer.render(layout, display_manager._last_data or {})
            img = frame.to_image()
            img = img.resize((256, 256), resample=0)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            return {"image": f"data:image/png;base64,{b64}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- Device APIs ---

    @app.post("/api/send")
    async def send_to_device() -> dict[str, Any]:
        """Send current frame to device."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")
        if display_manager.send_to_device():
            return {"success": True}
        else:
            raise HTTPException(status_code=500, detail="Failed to send to device")

    @app.post("/api/brightness/{level}")
    async def set_brightness(level: int) -> dict[str, Any]:
        """Set device brightness."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")
        if level < 0 or level > 100:
            raise HTTPException(status_code=400, detail="Brightness must be 0-100")
        display_manager.device.set_brightness(level)
        return {"success": True, "brightness": level}

    @app.get("/api/device/info")
    async def get_device_info() -> dict[str, Any]:
        """Get device information."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")
        try:
            info = display_manager.device.get_device_info()
            return {"success": True, "info": info}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/device/power")
    async def set_power(request: PowerRequest) -> dict[str, Any]:
        """Turn screen on or off."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")
        display_manager.device.set_screen_on(request.on)
        return {"success": True, "power": request.on}

    @app.post("/api/device/channel/{channel}")
    async def set_channel(channel: int) -> dict[str, Any]:
        """Set display channel (0=Faces, 1=Cloud, 2=Visualizer, 3=Custom, 4=Black)."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")
        if channel < 0 or channel > 4:
            raise HTTPException(status_code=400, detail="Channel must be 0-4")
        display_manager.device.set_channel(channel)
        return {"success": True, "channel": channel}

    @app.post("/api/device/reconnect")
    async def reconnect_device() -> dict[str, Any]:
        """Attempt to reconnect to the device."""
        try:
            # Try to reconnect using stored IP
            if display_manager._device_ip:
                from divoom_client.core.pixoo import Pixoo
                display_manager._device = Pixoo(display_manager._device_ip)
                return {"success": True, "ip": display_manager._device_ip}
            else:
                raise HTTPException(status_code=400, detail="No device IP configured")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/device/ping")
    async def ping_device() -> dict[str, Any]:
        """Check if device is reachable."""
        if not display_manager.device:
            return {"connected": False}
        try:
            display_manager.device.get_device_info()
            return {"connected": True, "ip": display_manager.device.ip_address}
        except Exception:
            return {"connected": False}

    @app.post("/api/device/scan")
    async def scan_for_devices() -> dict[str, Any]:
        """Scan network for Pixoo devices."""
        from divoom_client.core.discovery import scan_network
        try:
            devices = scan_network()
            return {"devices": devices, "count": len(devices)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/device/connect/{ip}")
    async def connect_to_device(ip: str) -> dict[str, Any]:
        """Connect to a specific device and save to config."""
        from divoom_client.core.discovery import save_device_config
        from divoom_client.models.config import DeviceConfig
        try:
            display_manager.connect(ip)
            config = DeviceConfig(ip_address=ip)
            config_path = display_manager.config_dir / "device.json"
            save_device_config(config, config_path)
            return {"success": True, "ip": ip}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # --- Data Source APIs ---

    @app.get("/api/datasources")
    async def list_datasources() -> dict[str, Any]:
        """List configured data sources."""
        sources = {}
        for name, source in display_manager._data_manager.sources.items():
            sources[name] = {
                "type": source.source_type,
                "refresh_seconds": source.config.refresh_seconds,
                "enabled": source.config.enabled,
                "last_fetch": source.last_fetch.isoformat() if source.last_fetch else None,
                "error": source.last_error,
            }
        return sources

    @app.get("/api/datasources/config")
    async def get_datasources_config() -> dict[str, Any]:
        """Get full data sources configuration."""
        config_path = display_manager.config_dir / "datasources.json"
        if not config_path.exists():
            return {"sources": {}}
        with open(config_path) as f:
            return json.load(f)

    @app.post("/api/datasources/{name}")
    async def create_datasource(name: str, request: DataSourceCreate) -> dict[str, Any]:
        """Create a new data source."""
        config_path = display_manager.config_dir / "datasources.json"

        # Load existing config
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
        else:
            config = {"sources": {}}

        if name in config["sources"]:
            raise HTTPException(status_code=400, detail=f"Data source already exists: {name}")

        # Add new source
        config["sources"][name] = request.config

        # Save config
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Register with manager
        try:
            display_manager._data_manager.create_source(name, request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {"success": True, "name": name}

    @app.put("/api/datasources/{name}")
    async def update_datasource(name: str, request: DataSourceCreate) -> dict[str, Any]:
        """Update a data source configuration."""
        config_path = display_manager.config_dir / "datasources.json"

        if not config_path.exists():
            raise HTTPException(status_code=404, detail="No data sources configured")

        with open(config_path) as f:
            config = json.load(f)

        if name not in config["sources"]:
            raise HTTPException(status_code=404, detail=f"Data source not found: {name}")

        # Update config
        config["sources"][name] = request.config

        # Save config
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Re-register source
        display_manager._data_manager.unregister(name)
        try:
            display_manager._data_manager.create_source(name, request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {"success": True, "name": name}

    @app.delete("/api/datasources/{name}")
    async def delete_datasource(name: str) -> dict[str, Any]:
        """Delete a data source."""
        config_path = display_manager.config_dir / "datasources.json"

        if not config_path.exists():
            raise HTTPException(status_code=404, detail="No data sources configured")

        with open(config_path) as f:
            config = json.load(f)

        if name not in config["sources"]:
            raise HTTPException(status_code=404, detail=f"Data source not found: {name}")

        # Remove from config
        del config["sources"][name]

        # Save config
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Unregister from manager
        display_manager._data_manager.unregister(name)

        return {"success": True}

    @app.post("/api/datasources/{name}/test")
    async def test_datasource(name: str) -> dict[str, Any]:
        """Test a data source and return fetched data."""
        source = display_manager._data_manager.get_source(name)
        if not source:
            raise HTTPException(status_code=404, detail=f"Data source not found: {name}")

        try:
            data = await source.refresh()
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @app.post("/api/datasources/{name}/toggle")
    async def toggle_datasource(name: str) -> dict[str, Any]:
        """Enable/disable a data source."""
        config_path = display_manager.config_dir / "datasources.json"

        if not config_path.exists():
            raise HTTPException(status_code=404, detail="No data sources configured")

        with open(config_path) as f:
            config = json.load(f)

        if name not in config["sources"]:
            raise HTTPException(status_code=404, detail=f"Data source not found: {name}")

        # Toggle enabled state
        current = config["sources"][name].get("enabled", True)
        config["sources"][name]["enabled"] = not current

        # Save config
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        return {"success": True, "enabled": not current}

    # --- Quick Action APIs ---

    @app.post("/api/quick/text")
    async def quick_text(request: QuickTextRequest) -> dict[str, Any]:
        """Send text directly to the display."""
        from divoom_client.core.frame import Frame
        from divoom_client.core.fonts import get_font
        from divoom_client.core.renderer import parse_color

        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")

        # Create frame with text
        frame = Frame(request.background)
        font = get_font(request.font)
        color = parse_color(request.color)

        # Render text
        x_offset = request.x
        for char in request.text:
            pixels = font.render_char(char, color)
            for px, py, c in pixels:
                frame.set_pixel(x_offset + px, request.y + py, c)
            x_offset += font.width + font.spacing

        # Send to device
        display_manager.device.send_pixels(frame.to_pixels())

        return {"success": True}

    @app.post("/api/quick/image")
    async def quick_image(file: UploadFile = File(...)) -> dict[str, Any]:
        """Upload and display an image."""
        if not display_manager.device:
            raise HTTPException(status_code=400, detail="No device connected")

        try:
            contents = await file.read()
            img = Image.open(io.BytesIO(contents))
            img = img.convert("RGB")
            img = img.resize((64, 64), resample=Image.Resampling.NEAREST)
            display_manager.device.send_image(img)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/quick/presets")
    async def list_presets() -> list[str]:
        """List available layout presets."""
        layouts_dir = display_manager.config_dir / "layouts"
        if not layouts_dir.exists():
            return []
        return sorted([p.stem for p in layouts_dir.glob("*.json")])

    @app.post("/api/quick/preset/{name}")
    async def activate_preset(name: str) -> dict[str, Any]:
        """Activate a preset layout."""
        layout_path = display_manager.config_dir / "layouts" / f"{name}.json"
        if not display_manager.load_layout(layout_path):
            raise HTTPException(status_code=400, detail=f"Failed to load preset: {name}")
        display_manager._render_and_send()
        return {"success": True, "preset": name}

    # --- Web UI ---

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the main web UI."""
        return get_index_html()

    return app


def get_index_html() -> str:
    """Return the main HTML page with tabbed interface."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Divoom Client</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f0f23;
            color: #eee;
            min-height: 100vh;
        }

        /* Header */
        header {
            background: #1a1a2e;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #333;
        }
        header h1 { color: #4cc9f0; font-size: 1.5em; }
        .connection-status {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.9em;
            color: #888;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #f72585;
        }
        .status-dot.connected { background: #4ade80; }

        /* Tabs */
        .tabs {
            display: flex;
            background: #16213e;
            border-bottom: 1px solid #333;
        }
        .tab {
            padding: 12px 24px;
            background: transparent;
            border: none;
            color: #888;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
            border-bottom: 2px solid transparent;
        }
        .tab:hover { color: #4cc9f0; background: rgba(76, 201, 240, 0.1); }
        .tab.active {
            color: #4cc9f0;
            border-bottom-color: #4cc9f0;
            background: rgba(76, 201, 240, 0.05);
        }

        /* Tab Content */
        .tab-content {
            display: none;
            padding: 20px;
            min-height: calc(100vh - 120px);
        }
        .tab-content.active { display: block; }

        /* Cards */
        .card {
            background: #16213e;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }
        .card h2 {
            color: #7b2cbf;
            margin-bottom: 15px;
            font-size: 1.1em;
            border-bottom: 1px solid #333;
            padding-bottom: 10px;
        }
        .card h3 {
            color: #4cc9f0;
            margin: 15px 0 10px;
            font-size: 1em;
        }

        /* Buttons */
        button {
            background: #4cc9f0;
            color: #000;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s;
        }
        button:hover { background: #7b2cbf; color: #fff; }
        button:disabled { background: #333; color: #666; cursor: not-allowed; }
        button.secondary {
            background: #333;
            color: #ccc;
        }
        button.secondary:hover { background: #444; }
        button.danger { background: #f72585; color: #fff; }
        button.danger:hover { background: #c41c68; }
        button.pending { background: #f59e0b; color: #000; }
        button.pending:hover { background: #d97706; }
        .button-group { display: flex; gap: 8px; flex-wrap: wrap; }

        /* Forms */
        input, select, textarea {
            background: #0f0f23;
            border: 1px solid #333;
            color: #fff;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 14px;
            width: 100%;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #4cc9f0;
        }
        input[type="color"] {
            width: 50px;
            height: 34px;
            padding: 2px;
            cursor: pointer;
        }
        input[type="range"] {
            -webkit-appearance: none;
            background: #333;
            height: 6px;
            border-radius: 3px;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 16px;
            height: 16px;
            background: #4cc9f0;
            border-radius: 50%;
            cursor: pointer;
        }
        label {
            display: block;
            margin-bottom: 12px;
            color: #888;
            font-size: 13px;
        }
        label span { color: #ccc; }

        /* Dashboard Grid */
        .dashboard-grid {
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 20px;
        }
        @media (max-width: 900px) {
            .dashboard-grid { grid-template-columns: 1fr; }
        }

        /* Preview */
        .preview-container { text-align: center; }
        .preview {
            image-rendering: pixelated;
            border: 2px solid #333;
            background: #000;
            max-width: 100%;
        }

        /* Status Items */
        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #2a2a4a;
        }
        .status-item:last-child { border-bottom: none; }
        .status-label { color: #888; }
        .status-value { color: #4cc9f0; }
        .status-value.error { color: #f72585; }
        .status-value.success { color: #4ade80; }

        /* Layout List */
        .layout-list { list-style: none; }
        .layout-item {
            padding: 10px 12px;
            margin: 4px 0;
            background: #0f0f23;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.2s;
        }
        .layout-item:hover { background: #1a1a4e; }
        .layout-item.active {
            border-left: 3px solid #4cc9f0;
            background: #1a1a4e;
        }

        /* Data Display */
        .data-source {
            background: #0f0f23;
            border-radius: 4px;
            padding: 12px;
            margin: 8px 0;
            font-family: monospace;
            font-size: 12px;
        }
        .data-source h4 {
            color: #7b2cbf;
            margin-bottom: 8px;
        }
        .data-source pre {
            white-space: pre-wrap;
            word-break: break-all;
        }

        /* Log */
        #log {
            background: #0f0f23;
            padding: 10px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 11px;
            max-height: 200px;
            overflow-y: auto;
        }
        .log-entry { padding: 2px 0; color: #666; }
        .log-entry.info { color: #4cc9f0; }
        .log-entry.error { color: #f72585; }
        .log-entry.success { color: #4ade80; }

        /* Device Controls */
        .control-row {
            display: flex;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #2a2a4a;
            gap: 15px;
        }
        .control-row:last-child { border-bottom: none; }
        .control-label {
            width: 100px;
            color: #888;
            font-size: 13px;
        }
        .control-value { flex: 1; }
        .channel-buttons {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        .channel-buttons button {
            padding: 6px 12px;
            font-size: 12px;
        }
        .channel-buttons button.active {
            background: #7b2cbf;
            color: #fff;
        }
        .power-btn {
            width: 70px;
            padding: 10px;
        }
        .power-btn.on { background: #4ade80; }
        .power-btn.off { background: #f72585; color: #fff; }

        /* Editor */
        .editor-grid {
            display: grid;
            grid-template-columns: auto 300px;
            gap: 20px;
            justify-content: start;
        }
        @media (max-width: 1000px) {
            .editor-grid { grid-template-columns: 1fr; }
        }
        .canvas-container {
            background: #16213e;
            border-radius: 8px;
            padding: 15px;
            max-width: 560px;
        }
        .canvas-wrapper {
            position: relative;
            width: 512px;
            min-width: 512px;
            max-width: 512px;
            height: 512px;
            min-height: 512px;
            max-height: 512px;
            margin: 0 auto;
            border: 2px solid #333;
            background: #000;
            box-sizing: content-box;
        }
        #layout-canvas {
            display: block;
            width: 512px !important;
            height: 512px !important;
            image-rendering: -moz-crisp-edges;
            image-rendering: -webkit-crisp-edges;
            image-rendering: pixelated;
            image-rendering: crisp-edges;
            cursor: crosshair;
        }
        .canvas-grid {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background-image:
                linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
            background-size: 8px 8px;
        }
        .toolbar {
            display: flex;
            gap: 8px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }
        .property-panel {
            background: #16213e;
            border-radius: 8px;
            padding: 15px;
        }
        .widget-list {
            margin-top: 15px;
        }
        .widget-item {
            padding: 8px 12px;
            margin: 4px 0;
            background: #0f0f23;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
        }
        .widget-item:hover { background: #1a1a4e; }
        .widget-item.selected {
            border: 1px solid #4cc9f0;
            background: #1a1a4e;
        }
        .widget-type { color: #7b2cbf; font-weight: 600; }
        .widget-info { color: #666; font-size: 11px; }

        /* Data Sources */
        .sources-grid {
            display: grid;
            grid-template-columns: 250px 1fr;
            gap: 20px;
        }
        @media (max-width: 800px) {
            .sources-grid { grid-template-columns: 1fr; }
        }
        .source-list { list-style: none; }
        .source-item {
            padding: 12px;
            margin: 4px 0;
            background: #0f0f23;
            border-radius: 4px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .source-item:hover { background: #1a1a4e; }
        .source-item.active { border-left: 3px solid #4cc9f0; }
        .source-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }
        .source-status-dot.ok { background: #4ade80; }
        .source-status-dot.error { background: #f72585; }
        .source-status-dot.disabled { background: #666; }
        .test-results {
            margin-top: 15px;
            padding: 12px;
            background: #0f0f23;
            border-radius: 4px;
            font-family: monospace;
            font-size: 12px;
            max-height: 300px;
            overflow: auto;
        }

        /* Quick Actions */
        .quick-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
        }
        .quick-card {
            background: #16213e;
            border-radius: 8px;
            padding: 20px;
        }
        .quick-card h3 {
            color: #4cc9f0;
            margin-bottom: 15px;
        }
        .preset-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
        }
        .image-preview {
            width: 128px;
            height: 128px;
            margin: 10px 0;
            background: #000;
            border: 1px solid #333;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #666;
        }
        .image-preview img {
            max-width: 100%;
            max-height: 100%;
            image-rendering: pixelated;
        }
    </style>
</head>
<body>
    <header>
        <h1>Divoom Client</h1>
        <div class="connection-status">
            <span class="status-dot" id="header-status-dot"></span>
            <span id="header-status-text">Checking...</span>
        </div>
    </header>

    <nav class="tabs">
        <button class="tab active" data-tab="dashboard">Dashboard</button>
        <button class="tab" data-tab="editor">Layout Editor</button>
        <button class="tab" data-tab="sources">Data Sources</button>
        <button class="tab" data-tab="device">Device</button>
        <button class="tab" data-tab="quick">Quick Actions</button>
    </nav>

    <!-- Dashboard Tab -->
    <div id="tab-dashboard" class="tab-content active">
        <div class="dashboard-grid">
            <div class="sidebar">
                <div class="card preview-container">
                    <img id="preview" class="preview" width="320" height="320" style="image-rendering: pixelated;" alt="Display Preview">
                    <p style="text-align:center;color:#666;font-size:11px;margin:5px 0 0 0;">Server-rendered preview (matches device)</p>
                    <div class="button-group" style="margin-top: 10px; justify-content: center;">
                        <button onclick="refreshAllData()">Refresh Data</button>
                        <button onclick="sendToDevice()">Send to Device</button>
                    </div>
                </div>

                <div class="card">
                    <h2>Status</h2>
                    <div id="status">Loading...</div>
                </div>

                <div class="card">
                    <h2>Layouts</h2>
                    <ul id="layouts" class="layout-list"></ul>
                </div>

                <div class="card">
                    <h2>Controls</h2>
                    <div class="button-group">
                        <button onclick="refreshAllData()">Refresh All Data</button>
                    </div>
                    <div style="margin-top: 15px;">
                        <label>Brightness: <span id="brightness-value">100</span>%</label>
                        <input type="range" id="brightness" min="0" max="100" value="100"
                               oninput="updateBrightnessLabel(this.value)"
                               onchange="setBrightness(this.value)">
                    </div>
                </div>
            </div>

            <div class="main">
                <div class="card">
                    <h2>Live Data</h2>
                    <div id="data" class="data-section">Loading...</div>
                </div>

                <div class="card">
                    <h2>Activity Log</h2>
                    <div id="log"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Layout Editor Tab -->
    <div id="tab-editor" class="tab-content">
        <div class="editor-grid">
            <div class="canvas-container">
                <div class="toolbar">
                    <button onclick="addWidget('text')">+ Text</button>
                    <button onclick="addWidget('rect')">+ Rectangle</button>
                    <button onclick="addWidget('line')">+ Line</button>
                    <button onclick="addWidget('clock')">+ Clock</button>
                    <span style="border-left: 1px solid #444; margin: 0 8px;"></span>
                    <button id="undo-btn" onclick="undo()" disabled title="Undo (Ctrl+Z)">Undo</button>
                    <button id="redo-btn" onclick="redo()" disabled title="Redo (Ctrl+Y)">Redo</button>
                    <span style="border-left: 1px solid #444; margin: 0 8px;"></span>
                    <button class="secondary" onclick="toggleGrid()">Toggle Grid</button>
                    <button class="danger" onclick="deleteSelectedWidget()">Delete</button>
                    <button onclick="saveCurrentLayout()">Save Layout</button>
                </div>
                <div class="canvas-wrapper">
                    <canvas id="layout-canvas" width="512" height="512"></canvas>
                    <div class="canvas-grid" id="canvas-grid"></div>
                </div>
            </div>

            <div class="sidebar">
                <div class="property-panel">
                    <h2>Properties</h2>
                    <div id="property-content">
                        <p style="color: #666; font-size: 13px;">Select a widget to edit its properties</p>
                    </div>
                </div>

                <div class="card">
                    <h2>Widgets</h2>
                    <div id="widget-list"></div>
                </div>

                <div class="card">
                    <h2>Layout</h2>
                    <label>Current: <span id="current-layout-name">None</span></label>
                    <div class="button-group">
                        <button onclick="showNewLayoutDialog()">New Layout</button>
                        <button class="danger" onclick="deleteCurrentLayout()">Delete</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Data Sources Tab -->
    <div id="tab-sources" class="tab-content">
        <div class="sources-grid">
            <div>
                <div class="card">
                    <h2>Data Sources</h2>
                    <ul id="source-list" class="source-list"></ul>
                </div>
                <div class="card">
                    <h2>Add New Source</h2>
                    <label>Type:
                        <select id="new-source-type">
                            <option value="stocks">Stocks (Yahoo Finance)</option>
                            <option value="weather">Weather (OpenWeatherMap)</option>
                            <option value="generic">Generic REST API</option>
                        </select>
                    </label>
                    <label>Name:
                        <input type="text" id="new-source-name" placeholder="my_source">
                    </label>
                    <button onclick="addDataSource()">Add Source</button>
                </div>
            </div>

            <div id="source-detail" class="card">
                <h2>Source Configuration</h2>
                <div id="source-config-content">
                    <p style="color: #666; font-size: 13px;">Select a data source to configure</p>
                </div>
            </div>
        </div>
    </div>

    <!-- Device Tab -->
    <div id="tab-device" class="tab-content">
        <div class="dashboard-grid">
            <div>
                <div class="card">
                    <h2>Device Information</h2>
                    <div id="device-info">Loading...</div>
                </div>

                <div class="card">
                    <h2>Device Controls</h2>
                    <div class="control-row">
                        <span class="control-label">Power</span>
                        <div class="control-value">
                            <button id="power-btn" class="power-btn on" onclick="togglePower()">ON</button>
                        </div>
                    </div>
                    <div class="control-row">
                        <span class="control-label">Brightness</span>
                        <div class="control-value">
                            <input type="range" id="device-brightness" min="0" max="100" value="100"
                                   oninput="document.getElementById('device-brightness-val').textContent = this.value + '%'"
                                   onchange="setBrightness(this.value)">
                            <span id="device-brightness-val" style="margin-left: 10px;">100%</span>
                        </div>
                    </div>
                    <div class="control-row">
                        <span class="control-label">Channel</span>
                        <div class="control-value channel-buttons">
                            <button onclick="setChannel(0)" title="Clock faces">Clock</button>
                            <button onclick="setChannel(1)" title="Cloud gallery">Cloud</button>
                            <button onclick="setChannel(2)" title="Visualizer">VU</button>
                            <button onclick="setChannel(3)" title="Custom content" class="active">Custom</button>
                            <button onclick="setChannel(4)" title="Screen off">Off</button>
                        </div>
                    </div>
                </div>
            </div>

            <div>
                <div class="card">
                    <h2>Connection</h2>
                    <div class="control-row">
                        <span class="control-label">Status</span>
                        <div class="control-value">
                            <span id="connection-status" class="status-value">Checking...</span>
                        </div>
                    </div>
                    <div class="control-row">
                        <span class="control-label">IP Address</span>
                        <div class="control-value">
                            <span id="device-ip">-</span>
                        </div>
                    </div>
                    <div style="margin-top: 15px;">
                        <button onclick="reconnectDevice()">Reconnect</button>
                        <button class="secondary" onclick="pingDevice()">Test Connection</button>
                    </div>
                </div>

                <div class="card">
                    <h2>Device Discovery</h2>
                    <p style="color: #888; margin-bottom: 15px;">Scan network for Pixoo devices</p>
                    <div style="margin-bottom: 15px;">
                        <button id="scan-btn" onclick="scanForDevices()">Scan Network</button>
                    </div>
                    <div id="scan-results"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Quick Actions Tab -->
    <div id="tab-quick" class="tab-content">
        <div class="quick-grid">
            <div class="quick-card">
                <h3>Send Text</h3>
                <label>Text:
                    <input type="text" id="quick-text" placeholder="Hello!" maxlength="20">
                </label>
                <div style="display: flex; gap: 15px;">
                    <label style="flex: 1;">Color:
                        <input type="color" id="quick-text-color" value="#ffffff">
                    </label>
                    <label style="flex: 1;">Background:
                        <input type="color" id="quick-text-bg" value="#000000">
                    </label>
                </div>
                <label>Font:
                    <select id="quick-text-font">
                        <option value="5x7">5x7 (larger)</option>
                        <option value="4x6">4x6 (smaller)</option>
                    </select>
                </label>
                <button onclick="sendQuickText()">Send to Display</button>
            </div>

            <div class="quick-card">
                <h3>Show Image</h3>
                <input type="file" id="quick-image" accept="image/*" onchange="previewQuickImage(this)">
                <div class="image-preview" id="quick-image-preview">
                    <span>No image</span>
                </div>
                <button onclick="sendQuickImage()">Send to Display</button>
            </div>

            <div class="quick-card">
                <h3>Presets</h3>
                <div id="preset-grid" class="preset-grid"></div>
            </div>

            <div class="quick-card">
                <h3>Quick Clear</h3>
                <p style="color: #888; margin-bottom: 15px; font-size: 13px;">Clear the display with a solid color</p>
                <label>Color:
                    <input type="color" id="clear-color" value="#000000">
                </label>
                <button onclick="clearDisplay()">Clear Display</button>
            </div>
        </div>
    </div>

    <script>
        // ==================== Global State ====================
        let currentLayout = null;
        let currentLayoutData = null;
        let selectedWidget = null;
        let selectedSourceName = null;
        let powerState = true;
        let gridVisible = true;
        let pendingChanges = {};  // Track unsaved property changes

        // Undo/Redo history
        let undoStack = [];
        let redoStack = [];
        const MAX_HISTORY = 50;

        // Drag state
        let isDragging = false;
        let dragStartX = 0;
        let dragStartY = 0;
        let dragWidgetStartX = 0;
        let dragWidgetStartY = 0;
        let dragWidgetStartX2 = 0;  // For line widgets
        let dragWidgetStartY2 = 0;  // For line widgets
        let dragHistorySaved = false;  // Track if we saved history for this drag

        // ==================== Tab Navigation ====================
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById('tab-' + tab.dataset.tab).classList.add('active');

                // Refresh relevant content when switching tabs
                if (tab.dataset.tab === 'editor') loadEditorData();
                if (tab.dataset.tab === 'sources') loadSourcesData();
                if (tab.dataset.tab === 'device') loadDeviceInfo();
                if (tab.dataset.tab === 'quick') loadPresets();
            });
        });

        // ==================== Logging ====================
        function log(message, type = '') {
            const logEl = document.getElementById('log');
            const entry = document.createElement('div');
            entry.className = 'log-entry ' + type;
            entry.textContent = new Date().toLocaleTimeString() + ' - ' + message;
            logEl.insertBefore(entry, logEl.firstChild);
            if (logEl.children.length > 50) logEl.removeChild(logEl.lastChild);
        }

        // ==================== Dashboard Functions ====================
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const status = await res.json();

                let html = '';
                html += `<div class="status-item"><span class="status-label">Device</span>
                         <span class="status-value ${status.device_connected ? 'success' : 'error'}">
                         ${status.device_ip || 'Not connected'}</span></div>`;
                html += `<div class="status-item"><span class="status-label">Layout</span>
                         <span class="status-value">${status.layout_name || 'None'}</span></div>`;
                html += `<div class="status-item"><span class="status-label">Scheduler</span>
                         <span class="status-value ${status.scheduler_running ? 'success' : 'error'}">
                         ${status.scheduler_running ? 'Running' : 'Stopped'}</span></div>`;
                html += `<div class="status-item"><span class="status-label">Jobs</span>
                         <span class="status-value">${status.scheduled_jobs.length}</span></div>`;

                document.getElementById('status').innerHTML = html;
                currentLayout = status.layout_name;

                // Update header status
                const dot = document.getElementById('header-status-dot');
                const text = document.getElementById('header-status-text');
                if (status.device_connected) {
                    dot.classList.add('connected');
                    text.textContent = status.device_ip;
                } else {
                    dot.classList.remove('connected');
                    text.textContent = 'Disconnected';
                }
            } catch (e) {
                log('Failed to fetch status: ' + e, 'error');
            }
        }

        async function fetchData() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();

                let html = '';
                for (const [source, values] of Object.entries(data)) {
                    html += `<div class="data-source">
                             <h4>${source}</h4>
                             <pre>${JSON.stringify(values, null, 2)}</pre>
                             </div>`;
                }
                document.getElementById('data').innerHTML = html || '<p style="color:#666">No data</p>';
            } catch (e) {
                log('Failed to fetch data: ' + e, 'error');
            }
        }

        async function fetchLayouts() {
            try {
                const res = await fetch('/api/layouts');
                const layouts = await res.json();

                let html = '';
                for (const name of layouts) {
                    const isActive = name === currentLayout;
                    html += `<li class="layout-item ${isActive ? 'active' : ''}" onclick="loadLayout('${name}')">
                             <span>${name}</span>
                             ${isActive ? '<span style="color:#4cc9f0">●</span>' : ''}
                             </li>`;
                }
                document.getElementById('layouts').innerHTML = html || '<li class="layout-item">No layouts</li>';
            } catch (e) {
                log('Failed to fetch layouts: ' + e, 'error');
            }
        }

        async function refreshPreview() {
            try {
                const res = await fetch('/api/preview/base64');
                const data = await res.json();
                document.getElementById('preview').src = data.image;
            } catch (e) {
                log('Failed to refresh preview: ' + e, 'error');
            }
        }

        async function sendToDevice() {
            try {
                const res = await fetch('/api/send', { method: 'POST' });
                const data = await res.json();
                if (data.success) log('Sent to device', 'success');
            } catch (e) {
                log('Failed to send to device: ' + e, 'error');
            }
        }

        async function refreshAllData() {
            // Find all refresh buttons and disable them
            const buttons = document.querySelectorAll('button');
            buttons.forEach(btn => {
                if (btn.textContent.includes('Refresh')) {
                    btn.disabled = true;
                    btn.dataset.originalText = btn.textContent;
                    btn.textContent = 'Refreshing...';
                }
            });

            try {
                log('Refreshing all data...', 'info');
                const res = await fetch('/api/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await res.json();
                if (data.success) {
                    log('Data refreshed successfully', 'success');
                    await fetchData();
                    await refreshPreview();
                } else {
                    log('Refresh returned error', 'error');
                }
            } catch (e) {
                log('Failed to refresh data: ' + e, 'error');
            } finally {
                // Re-enable buttons
                buttons.forEach(btn => {
                    if (btn.dataset.originalText) {
                        btn.textContent = btn.dataset.originalText;
                        btn.disabled = false;
                        delete btn.dataset.originalText;
                    }
                });
            }
        }

        async function loadLayout(name) {
            try {
                const res = await fetch('/api/layout/load/' + name, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    log('Loaded layout: ' + name, 'success');
                    currentLayout = name;
                    await fetchLayouts();
                    await refreshPreview();
                }
            } catch (e) {
                log('Failed to load layout: ' + e, 'error');
            }
        }

        function updateBrightnessLabel(value) {
            document.getElementById('brightness-value').textContent = value;
        }

        async function setBrightness(value) {
            try {
                await fetch('/api/brightness/' + value, { method: 'POST' });
                log('Brightness: ' + value + '%', 'info');
            } catch (e) {
                log('Failed to set brightness: ' + e, 'error');
            }
        }

        // ==================== Editor Functions ====================
        const canvas = document.getElementById('layout-canvas');
        const ctx = canvas.getContext('2d');
        ctx.imageSmoothingEnabled = false;  // Disable anti-aliasing for pixelated look
        const SCALE = 8;

        async function loadEditorData() {
            if (!currentLayout) {
                await fetchStatus();
            }
            if (currentLayout) {
                try {
                    const res = await fetch('/api/layout');
                    currentLayoutData = await res.json();
                    document.getElementById('current-layout-name').textContent = currentLayout;
                    // Clear history when loading new layout
                    undoStack = [];
                    redoStack = [];
                    updateUndoRedoButtons();
                    renderCanvas();
                    updateWidgetList();
                } catch (e) {
                    log('Failed to load layout for editor: ' + e, 'error');
                }
            }
        }

        // ==================== Undo/Redo ====================
        function saveToHistory() {
            if (!currentLayoutData) return;
            undoStack.push(JSON.stringify(currentLayoutData));
            if (undoStack.length > MAX_HISTORY) undoStack.shift();
            redoStack = [];  // Clear redo on new action
            updateUndoRedoButtons();
        }

        function undo() {
            if (undoStack.length === 0) return;
            redoStack.push(JSON.stringify(currentLayoutData));
            currentLayoutData = JSON.parse(undoStack.pop());
            selectedWidget = null;
            pendingChanges = {};
            renderCanvas();
            updateWidgetList();
            updatePropertyPanel();
            updateUndoRedoButtons();
            saveLayoutToServer();
        }

        function redo() {
            if (redoStack.length === 0) return;
            undoStack.push(JSON.stringify(currentLayoutData));
            currentLayoutData = JSON.parse(redoStack.pop());
            selectedWidget = null;
            pendingChanges = {};
            renderCanvas();
            updateWidgetList();
            updatePropertyPanel();
            updateUndoRedoButtons();
            saveLayoutToServer();
        }

        function updateUndoRedoButtons() {
            const undoBtn = document.getElementById('undo-btn');
            const redoBtn = document.getElementById('redo-btn');
            if (undoBtn) undoBtn.disabled = undoStack.length === 0;
            if (redoBtn) redoBtn.disabled = redoStack.length === 0;
        }

        async function saveLayoutToServer() {
            if (!currentLayout || !currentLayoutData) return;
            try {
                await fetch('/api/layouts/' + currentLayout, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({layout: currentLayoutData})
                });
            } catch (e) {
                log('Failed to save layout: ' + e, 'error');
            }
        }

        // Keyboard shortcuts for undo/redo
        document.addEventListener('keydown', (e) => {
            // Only handle if editor tab is active
            const editorTab = document.getElementById('tab-editor');
            if (!editorTab || !editorTab.classList.contains('active')) return;

            if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
                e.preventDefault();
                undo();
            } else if (e.ctrlKey && (e.key === 'y' || (e.key === 'Z' && e.shiftKey))) {
                e.preventDefault();
                redo();
            }
        });

        function renderCanvas() {
            if (!currentLayoutData) return;

            // Ensure no anti-aliasing
            ctx.imageSmoothingEnabled = false;

            // Clear with background
            ctx.fillStyle = currentLayoutData.background || '#000000';
            ctx.fillRect(0, 0, 512, 512);

            // Render widgets
            for (const widget of currentLayoutData.widgets || []) {
                renderWidget(widget);
            }

            // Draw selection
            if (selectedWidget) {
                drawSelection(selectedWidget);
            }
        }

        // Bitmap font data for accurate preview
        const FONT_5X7 = {
            ' ': [0,0,0,0,0,0,0], '!': [4,4,4,4,4,0,4], '"': [10,10,0,0,0,0,0],
            '$': [4,15,20,14,5,30,4], '%': [25,26,4,4,11,19,0], '0': [14,17,19,21,25,17,14],
            '1': [4,12,4,4,4,4,14], '2': [14,17,1,6,8,16,31], '3': [14,17,1,6,1,17,14],
            '4': [2,6,10,18,31,2,2], '5': [31,16,30,1,1,17,14], '6': [6,8,16,30,17,17,14],
            '7': [31,1,2,4,8,8,8], '8': [14,17,17,14,17,17,14], '9': [14,17,17,15,1,2,12],
            '.': [0,0,0,0,0,0,4], '-': [0,0,0,31,0,0,0], '+': [0,4,4,31,4,4,0],
            ':': [0,0,4,0,4,0,0], 'A': [14,17,17,31,17,17,17], 'B': [30,17,17,30,17,17,30],
            'C': [14,17,16,16,16,17,14], 'D': [30,17,17,17,17,17,30], 'E': [31,16,16,30,16,16,31],
            'F': [31,16,16,30,16,16,16], 'G': [14,17,16,23,17,17,14], 'H': [17,17,17,31,17,17,17],
            'I': [14,4,4,4,4,4,14], 'J': [7,2,2,2,2,18,12], 'K': [17,18,20,24,20,18,17],
            'L': [16,16,16,16,16,16,31], 'M': [17,27,21,21,17,17,17], 'N': [17,25,21,19,17,17,17],
            'O': [14,17,17,17,17,17,14], 'P': [30,17,17,30,16,16,16], 'Q': [14,17,17,17,21,18,13],
            'R': [30,17,17,30,20,18,17], 'S': [14,17,16,14,1,17,14], 'T': [31,4,4,4,4,4,4],
            'U': [17,17,17,17,17,17,14], 'V': [17,17,17,17,17,10,4], 'W': [17,17,17,21,21,27,17],
            'X': [17,17,10,4,10,17,17], 'Y': [17,17,10,4,4,4,4], 'Z': [31,1,2,4,8,16,31],
            'a': [0,0,14,1,15,17,15], 'b': [16,16,30,17,17,17,30], 'c': [0,0,14,16,16,17,14],
            'd': [1,1,15,17,17,17,15], 'e': [0,0,14,17,31,16,14], 'f': [6,8,30,8,8,8,8],
            'g': [0,0,15,17,15,1,14], 'h': [16,16,30,17,17,17,17], 'i': [4,0,12,4,4,4,14],
            'j': [2,0,6,2,2,18,12], 'k': [16,16,18,20,24,20,18], 'l': [12,4,4,4,4,4,14],
            'm': [0,0,26,21,21,21,21], 'n': [0,0,30,17,17,17,17], 'o': [0,0,14,17,17,17,14],
            'p': [0,0,30,17,30,16,16], 'q': [0,0,15,17,15,1,1], 'r': [0,0,22,24,16,16,16],
            's': [0,0,14,16,14,1,30], 't': [8,8,30,8,8,8,6], 'u': [0,0,17,17,17,19,13],
            'v': [0,0,17,17,17,10,4], 'w': [0,0,17,17,21,21,10], 'x': [0,0,17,10,4,10,17],
            'y': [0,0,17,17,15,1,14], 'z': [0,0,31,2,4,8,31]
        };
        const FONT_4X6 = {
            ' ': [0,0,0,0,0,0], '!': [4,4,4,4,0,4], '0': [6,9,9,9,9,6], '1': [2,6,2,2,2,7],
            '2': [6,9,2,4,8,15], '3': [6,9,2,1,9,6], '4': [2,6,10,15,2,2], '5': [15,8,14,1,9,6],
            '6': [6,8,14,9,9,6], '7': [15,1,2,4,4,4], '8': [6,9,6,9,9,6], '9': [6,9,7,1,1,6],
            '.': [0,0,0,0,0,4], '-': [0,0,0,15,0,0], '+': [0,4,4,14,4,4], ':': [0,4,0,0,4,0],
            'A': [6,9,9,15,9,9], 'B': [14,9,14,9,9,14], 'C': [6,9,8,8,9,6], 'D': [14,9,9,9,9,14],
            'E': [15,8,14,8,8,15], 'F': [15,8,14,8,8,8], 'G': [6,9,8,11,9,6], 'H': [9,9,15,9,9,9],
            'I': [14,4,4,4,4,14], 'J': [7,2,2,2,10,4], 'K': [9,10,12,10,9,9], 'L': [8,8,8,8,8,15],
            'M': [9,15,15,9,9,9], 'N': [9,13,11,9,9,9], 'O': [6,9,9,9,9,6], 'P': [14,9,9,14,8,8],
            'Q': [6,9,9,9,10,5], 'R': [14,9,9,14,10,9], 'S': [6,9,4,2,9,6], 'T': [14,4,4,4,4,4],
            'U': [9,9,9,9,9,6], 'V': [9,9,9,9,6,6], 'W': [9,9,9,15,15,9], 'X': [9,9,6,6,9,9],
            'Y': [10,10,10,4,4,4], 'Z': [15,1,2,4,8,15],
            'a': [0,6,1,7,9,7], 'b': [8,8,14,9,9,14], 'c': [0,0,6,8,8,6], 'd': [1,1,7,9,9,7],
            'e': [0,6,9,15,8,6], 'f': [2,4,14,4,4,4], 'g': [0,7,9,7,1,6], 'h': [8,8,14,9,9,9],
            'i': [4,0,4,4,4,4], 'j': [2,0,2,2,10,4], 'k': [8,8,10,12,10,9], 'l': [4,4,4,4,4,2],
            'm': [0,0,10,15,9,9], 'n': [0,0,14,9,9,9], 'o': [0,0,6,9,9,6], 'p': [0,14,9,14,8,8],
            'q': [0,7,9,7,1,1], 'r': [0,0,10,12,8,8], 's': [0,6,8,6,1,14], 't': [4,4,14,4,4,2],
            'u': [0,0,9,9,9,6], 'v': [0,0,9,9,6,6], 'w': [0,0,9,9,15,15], 'x': [0,0,9,6,6,9],
            'y': [0,9,9,7,1,6], 'z': [0,0,15,2,4,15]
        };

        function renderBitmapChar(char, x, y, font, color) {
            const fontData = font === '4x6' ? FONT_4X6 : FONT_5X7;
            const charWidth = font === '4x6' ? 4 : 5;
            const bitmap = fontData[char] || fontData[char.toUpperCase()];
            if (!bitmap) {
                console.warn('Missing bitmap for char:', char, 'font:', font);
                return charWidth;
            }

            for (let row = 0; row < bitmap.length; row++) {
                for (let col = 0; col < charWidth; col++) {
                    if (bitmap[row] & (1 << (charWidth - 1 - col))) {
                        ctx.fillRect((x + col) * SCALE, (y + row) * SCALE, SCALE, SCALE);
                    }
                }
            }
            return charWidth;
        }

        function renderWidget(widget) {
            ctx.fillStyle = getWidgetColor(widget);
            ctx.strokeStyle = getWidgetColor(widget);

            switch (widget.type) {
                case 'text':
                    ctx.fillStyle = getWidgetColor(widget);
                    const text = widget.text || widget.data_source || '???';
                    const fontName = widget.font || '5x7';
                    const spacing = 1;
                    let xPos = widget.x;
                    for (const char of text) {
                        const charWidth = renderBitmapChar(char, xPos, widget.y, fontName, getWidgetColor(widget));
                        xPos += charWidth + spacing;
                    }
                    break;
                case 'rect':
                    if (widget.filled !== false) {
                        ctx.fillRect(widget.x * SCALE, widget.y * SCALE,
                                    (widget.width || 10) * SCALE, (widget.height || 10) * SCALE);
                    } else {
                        ctx.strokeRect(widget.x * SCALE, widget.y * SCALE,
                                      (widget.width || 10) * SCALE, (widget.height || 10) * SCALE);
                    }
                    break;
                case 'line':
                    ctx.beginPath();
                    ctx.moveTo((widget.x1 || 0) * SCALE, (widget.y1 || 0) * SCALE);
                    ctx.lineTo((widget.x2 || 63) * SCALE, (widget.y2 || 0) * SCALE);
                    ctx.lineWidth = SCALE;
                    ctx.stroke();
                    break;
                case 'clock':
                    ctx.fillStyle = getWidgetColor(widget);
                    const clockText = getClockPreviewText(widget);
                    const clockFont = widget.font || '5x7';
                    let clockX = widget.x;
                    for (const char of clockText) {
                        const charWidth = renderBitmapChar(char, clockX, widget.y, clockFont, getWidgetColor(widget));
                        clockX += charWidth + 1;
                    }
                    break;
            }
        }

        function getClockPreviewText(widget) {
            // Generate preview time string based on widget settings
            const now = new Date();
            // Apply timezone offset (widget.timezone_offset is hours from UTC)
            const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
            let offsetHours = widget.timezone_offset || 0;

            // Simple US DST detection for preview
            if (widget.auto_dst !== false) {
                const year = now.getFullYear();
                // Second Sunday in March
                const dstStart = new Date(year, 2, 8 + (7 - new Date(year, 2, 1).getDay()) % 7);
                // First Sunday in November
                const dstEnd = new Date(year, 10, 1 + (7 - new Date(year, 10, 1).getDay()) % 7);
                const testDate = new Date(utcMs + offsetHours * 3600000);
                if (testDate >= dstStart && testDate < dstEnd) {
                    offsetHours += 1;
                }
            }

            const localTime = new Date(utcMs + offsetHours * 3600000);
            let hours = localTime.getUTCHours();
            const mins = localTime.getUTCMinutes().toString().padStart(2, '0');
            const secs = localTime.getUTCSeconds().toString().padStart(2, '0');

            if (widget.format_24h) {
                const hStr = hours.toString().padStart(2, '0');
                return widget.show_seconds ? `${hStr}:${mins}:${secs}` : `${hStr}:${mins}`;
            } else {
                const ampm = hours >= 12 ? 'p' : 'a';
                hours = hours % 12 || 12;
                return widget.show_seconds ? `${hours}:${mins}:${secs}${ampm}` : `${hours}:${mins}${ampm}`;
            }
        }

        function getWidgetColor(widget) {
            if (typeof widget.color === 'string') return widget.color;
            if (widget.color && widget.color.default) return widget.color.default;
            return '#FFFFFF';
        }

        function drawSelection(widget) {
            ctx.strokeStyle = '#4cc9f0';
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 5]);

            let x, y, w, h;
            switch (widget.type) {
                case 'text':
                    // Calculate text bounds based on content and font
                    const text = widget.text || widget.data_source || '???';
                    const fontWidth = widget.font === '4x6' ? 4 : 5;
                    const fontHeight = widget.font === '4x6' ? 6 : 7;
                    x = widget.x * SCALE - 2;
                    y = widget.y * SCALE - 2;
                    w = text.length * (fontWidth + 1) * SCALE + 4;
                    h = fontHeight * SCALE + 4;
                    break;
                case 'rect':
                    x = widget.x * SCALE - 2;
                    y = widget.y * SCALE - 2;
                    w = (widget.width || 10) * SCALE + 4;
                    h = (widget.height || 10) * SCALE + 4;
                    break;
                case 'line':
                    const x1 = (widget.x1 || 0) * SCALE;
                    const y1 = (widget.y1 || 0) * SCALE;
                    const x2 = (widget.x2 || 63) * SCALE;
                    const y2 = (widget.y2 || 0) * SCALE;
                    x = Math.min(x1, x2) - 4;
                    y = Math.min(y1, y2) - 4;
                    w = Math.abs(x2 - x1) + 8;
                    h = Math.abs(y2 - y1) + 8;
                    // Ensure minimum dimensions for horizontal/vertical lines
                    if (w < SCALE) w = SCALE + 8;
                    if (h < SCALE) h = SCALE + 8;
                    break;
                case 'clock':
                    const clockPreview = getClockPreviewText(widget);
                    const clockFontW = widget.font === '4x6' ? 4 : 5;
                    const clockFontH = widget.font === '4x6' ? 6 : 7;
                    x = widget.x * SCALE - 2;
                    y = widget.y * SCALE - 2;
                    w = clockPreview.length * (clockFontW + 1) * SCALE + 4;
                    h = clockFontH * SCALE + 4;
                    break;
                default:
                    return;
            }
            ctx.strokeRect(x, y, w, h);
            ctx.setLineDash([]);
        }

        function updateWidgetList() {
            if (!currentLayoutData) return;

            let html = '';
            for (const widget of currentLayoutData.widgets || []) {
                const isSelected = selectedWidget && selectedWidget.id === widget.id;
                let info = '';
                if (widget.type === 'text') {
                    info = widget.text || widget.data_source || '';
                } else if (widget.type === 'clock') {
                    info = `UTC${widget.timezone_offset >= 0 ? '+' : ''}${widget.timezone_offset || 0}`;
                }
                html += `<div class="widget-item ${isSelected ? 'selected' : ''}" onclick="selectWidget('${widget.id}')">
                         <span><span class="widget-type">${widget.type}</span> ${widget.id || ''}</span>
                         <span class="widget-info">${info.substring(0, 15)}</span>
                         </div>`;
            }
            document.getElementById('widget-list').innerHTML = html || '<p style="color:#666;font-size:13px">No widgets</p>';
        }

        function selectWidget(id) {
            selectedWidget = (currentLayoutData.widgets || []).find(w => w.id === id) || null;
            renderCanvas();
            updateWidgetList();
            updatePropertyPanel();
        }

        function updatePropertyPanel() {
            const container = document.getElementById('property-content');
            if (!selectedWidget) {
                container.innerHTML = '<p style="color: #666; font-size: 13px;">Select a widget to edit its properties</p>';
                return;
            }

            // Reset pending changes when switching widgets
            pendingChanges = {};

            let html = `<h3 style="color:#7b2cbf;margin-bottom:15px;">${selectedWidget.type.toUpperCase()}</h3>`;

            switch (selectedWidget.type) {
                case 'text':
                    html += `
                        <label>X: <input type="number" id="prop-x" value="${selectedWidget.x || 0}" min="0" max="63" oninput="setPendingChange('x', parseInt(this.value))"></label>
                        <label>Y: <input type="number" id="prop-y" value="${selectedWidget.y || 0}" min="0" max="63" oninput="setPendingChange('y', parseInt(this.value))"></label>
                        <label>Font: <select id="prop-font" onchange="setPendingChange('font', this.value)">
                            <option value="5x7" ${selectedWidget.font === '5x7' ? 'selected' : ''}>5x7</option>
                            <option value="4x6" ${selectedWidget.font === '4x6' ? 'selected' : ''}>4x6</option>
                        </select></label>
                        <label>Static Text: <input type="text" id="prop-text" value="${selectedWidget.text || ''}" oninput="setPendingChange('text', this.value)"></label>
                        <label>Data Source: <input type="text" id="prop-datasource" value="${selectedWidget.data_source || ''}" placeholder="stocks.AAPL.price" oninput="setPendingChange('data_source', this.value)"></label>
                        <label>Format: <input type="text" id="prop-format" value="${selectedWidget.format || '{value}'}" oninput="setPendingChange('format', this.value)"></label>
                        <label>Color: <input type="color" id="prop-color" value="${getWidgetColor(selectedWidget)}" oninput="setPendingChange('color', this.value)"></label>
                    `;
                    break;
                case 'rect':
                    html += `
                        <label>X: <input type="number" id="prop-x" value="${selectedWidget.x || 0}" min="0" max="63" oninput="setPendingChange('x', parseInt(this.value))"></label>
                        <label>Y: <input type="number" id="prop-y" value="${selectedWidget.y || 0}" min="0" max="63" oninput="setPendingChange('y', parseInt(this.value))"></label>
                        <label>Width: <input type="number" id="prop-width" value="${selectedWidget.width || 10}" min="1" max="64" oninput="setPendingChange('width', parseInt(this.value))"></label>
                        <label>Height: <input type="number" id="prop-height" value="${selectedWidget.height || 10}" min="1" max="64" oninput="setPendingChange('height', parseInt(this.value))"></label>
                        <label>Color: <input type="color" id="prop-color" value="${getWidgetColor(selectedWidget)}" oninput="setPendingChange('color', this.value)"></label>
                        <label><input type="checkbox" id="prop-filled" ${selectedWidget.filled !== false ? 'checked' : ''} onchange="setPendingChange('filled', this.checked)"> Filled</label>
                    `;
                    break;
                case 'line':
                    html += `
                        <label>X1: <input type="number" id="prop-x1" value="${selectedWidget.x1 || 0}" min="0" max="63" oninput="setPendingChange('x1', parseInt(this.value))"></label>
                        <label>Y1: <input type="number" id="prop-y1" value="${selectedWidget.y1 || 0}" min="0" max="63" oninput="setPendingChange('y1', parseInt(this.value))"></label>
                        <label>X2: <input type="number" id="prop-x2" value="${selectedWidget.x2 || 63}" min="0" max="63" oninput="setPendingChange('x2', parseInt(this.value))"></label>
                        <label>Y2: <input type="number" id="prop-y2" value="${selectedWidget.y2 || 0}" min="0" max="63" oninput="setPendingChange('y2', parseInt(this.value))"></label>
                        <label>Color: <input type="color" id="prop-color" value="${getWidgetColor(selectedWidget)}" oninput="setPendingChange('color', this.value)"></label>
                    `;
                    break;
                case 'clock':
                    html += `
                        <label>X: <input type="number" id="prop-x" value="${selectedWidget.x || 0}" min="0" max="63" oninput="setPendingChange('x', parseInt(this.value))"></label>
                        <label>Y: <input type="number" id="prop-y" value="${selectedWidget.y || 0}" min="0" max="63" oninput="setPendingChange('y', parseInt(this.value))"></label>
                        <label>Font: <select id="prop-font" onchange="setPendingChange('font', this.value)">
                            <option value="5x7" ${selectedWidget.font === '5x7' ? 'selected' : ''}>5x7</option>
                            <option value="4x6" ${selectedWidget.font === '4x6' ? 'selected' : ''}>4x6</option>
                        </select></label>
                        <label><input type="checkbox" id="prop-format24h" ${selectedWidget.format_24h ? 'checked' : ''} onchange="setPendingChange('format_24h', this.checked)"> 24-Hour Format</label>
                        <label><input type="checkbox" id="prop-showsecs" ${selectedWidget.show_seconds ? 'checked' : ''} onchange="setPendingChange('show_seconds', this.checked)"> Show Seconds</label>
                        <label>UTC Offset: <select id="prop-tzoffset" onchange="setPendingChange('timezone_offset', parseFloat(this.value))">
                            <option value="-12" ${selectedWidget.timezone_offset === -12 ? 'selected' : ''}>UTC-12</option>
                            <option value="-11" ${selectedWidget.timezone_offset === -11 ? 'selected' : ''}>UTC-11</option>
                            <option value="-10" ${selectedWidget.timezone_offset === -10 ? 'selected' : ''}>UTC-10 (Hawaii)</option>
                            <option value="-9" ${selectedWidget.timezone_offset === -9 ? 'selected' : ''}>UTC-9 (Alaska)</option>
                            <option value="-8" ${selectedWidget.timezone_offset === -8 ? 'selected' : ''}>UTC-8 (Pacific)</option>
                            <option value="-7" ${selectedWidget.timezone_offset === -7 ? 'selected' : ''}>UTC-7 (Mountain)</option>
                            <option value="-6" ${selectedWidget.timezone_offset === -6 ? 'selected' : ''}>UTC-6 (Central)</option>
                            <option value="-5" ${selectedWidget.timezone_offset === -5 ? 'selected' : ''}>UTC-5 (Eastern)</option>
                            <option value="-4" ${selectedWidget.timezone_offset === -4 ? 'selected' : ''}>UTC-4 (Atlantic)</option>
                            <option value="-3" ${selectedWidget.timezone_offset === -3 ? 'selected' : ''}>UTC-3</option>
                            <option value="0" ${selectedWidget.timezone_offset === 0 ? 'selected' : ''}>UTC (GMT)</option>
                            <option value="1" ${selectedWidget.timezone_offset === 1 ? 'selected' : ''}>UTC+1 (CET)</option>
                            <option value="2" ${selectedWidget.timezone_offset === 2 ? 'selected' : ''}>UTC+2 (EET)</option>
                            <option value="3" ${selectedWidget.timezone_offset === 3 ? 'selected' : ''}>UTC+3 (Moscow)</option>
                            <option value="5.5" ${selectedWidget.timezone_offset === 5.5 ? 'selected' : ''}>UTC+5:30 (India)</option>
                            <option value="8" ${selectedWidget.timezone_offset === 8 ? 'selected' : ''}>UTC+8 (China)</option>
                            <option value="9" ${selectedWidget.timezone_offset === 9 ? 'selected' : ''}>UTC+9 (Japan)</option>
                            <option value="10" ${selectedWidget.timezone_offset === 10 ? 'selected' : ''}>UTC+10 (Sydney)</option>
                            <option value="12" ${selectedWidget.timezone_offset === 12 ? 'selected' : ''}>UTC+12 (NZ)</option>
                        </select></label>
                        <label><input type="checkbox" id="prop-autodst" ${selectedWidget.auto_dst !== false ? 'checked' : ''} onchange="setPendingChange('auto_dst', this.checked)"> Auto DST (US)</label>
                        <label>Color: <input type="color" id="prop-color" value="${getWidgetColor(selectedWidget)}" oninput="setPendingChange('color', this.value)"></label>
                    `;
                    break;
            }

            // Add Apply button
            html += `
                <div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #333;">
                    <button id="apply-btn" onclick="applyChanges()" disabled>Apply</button>
                </div>
            `;

            container.innerHTML = html;
        }

        function setPendingChange(prop, value) {
            pendingChanges[prop] = value;
            // Update local widget preview immediately
            if (selectedWidget) {
                selectedWidget[prop] = value;
                renderCanvas();
            }
            // Show unsaved indicator on Apply button
            const applyBtn = document.getElementById('apply-btn');
            if (applyBtn) {
                applyBtn.disabled = false;
                applyBtn.textContent = 'Apply *';
                applyBtn.classList.add('pending');
            }
        }

        async function applyChanges() {
            if (!selectedWidget || !selectedWidget.id) {
                log('No widget selected', 'error');
                return;
            }
            if (Object.keys(pendingChanges).length === 0) {
                log('No changes to apply', 'info');
                return;
            }

            // Save current state for undo before making changes
            saveToHistory();

            const applyBtn = document.getElementById('apply-btn');
            if (applyBtn) {
                applyBtn.disabled = true;
                applyBtn.textContent = 'Saving...';
            }

            try {
                const res = await fetch('/api/layout/widget/' + selectedWidget.id, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ updates: pendingChanges })
                });
                const data = await res.json();
                if (data.success) {
                    currentLayoutData = data.layout;
                    selectedWidget = currentLayoutData.widgets.find(w => w.id === selectedWidget.id);
                    pendingChanges = {};
                    renderCanvas();
                    await refreshPreview();
                    log('Changes applied', 'success');
                    if (applyBtn) {
                        applyBtn.textContent = 'Apply';
                        applyBtn.classList.remove('pending');
                    }
                } else {
                    log('Apply failed: ' + (data.error || 'Unknown error'), 'error');
                    if (applyBtn) {
                        applyBtn.disabled = false;
                        applyBtn.textContent = 'Apply *';
                    }
                }
            } catch (e) {
                log('Failed to apply changes: ' + e, 'error');
                if (applyBtn) {
                    applyBtn.disabled = false;
                    applyBtn.textContent = 'Apply *';
                }
            }
        }

        async function addWidget(type) {
            const widget = { type };
            switch (type) {
                case 'text':
                    widget.x = 2;
                    widget.y = 2;
                    widget.font = '5x7';
                    widget.text = 'Text';
                    widget.color = '#FFFFFF';
                    break;
                case 'rect':
                    widget.x = 10;
                    widget.y = 10;
                    widget.width = 20;
                    widget.height = 20;
                    widget.color = '#FFFFFF';
                    widget.filled = true;
                    break;
                case 'line':
                    widget.x1 = 0;
                    widget.y1 = 32;
                    widget.x2 = 63;
                    widget.y2 = 32;
                    widget.color = '#FFFFFF';
                    break;
                case 'clock':
                    widget.x = 2;
                    widget.y = 2;
                    widget.font = '5x7';
                    widget.format_24h = false;
                    widget.show_seconds = false;
                    widget.timezone_offset = -6;  // CST
                    widget.auto_dst = true;
                    widget.color = '#FFFFFF';
                    break;
            }

            // Save current state for undo before adding widget
            saveToHistory();

            try {
                const res = await fetch('/api/layout/widget', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ widget })
                });
                const data = await res.json();
                if (data.success) {
                    currentLayoutData = data.layout;
                    selectedWidget = currentLayoutData.widgets.find(w => w.id === data.widget_id);
                    renderCanvas();
                    updateWidgetList();
                    updatePropertyPanel();
                    await refreshPreview();
                    log('Added ' + type + ' widget', 'success');
                }
            } catch (e) {
                log('Failed to add widget: ' + e, 'error');
            }
        }

        async function deleteSelectedWidget() {
            if (!selectedWidget || !selectedWidget.id) {
                log('No widget selected', 'error');
                return;
            }

            if (!confirm('Delete this widget?')) return;

            // Save current state for undo before deleting widget
            saveToHistory();

            try {
                const res = await fetch('/api/layout/widget/' + selectedWidget.id, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    currentLayoutData = data.layout;
                    selectedWidget = null;
                    renderCanvas();
                    updateWidgetList();
                    updatePropertyPanel();
                    await refreshPreview();
                    log('Widget deleted', 'success');
                }
            } catch (e) {
                log('Failed to delete widget: ' + e, 'error');
            }
        }

        function toggleGrid() {
            gridVisible = !gridVisible;
            document.getElementById('canvas-grid').style.display = gridVisible ? 'block' : 'none';
        }

        async function saveCurrentLayout() {
            if (!currentLayoutData) return;
            try {
                const res = await fetch('/api/layouts/' + currentLayout, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ layout: currentLayoutData })
                });
                const data = await res.json();
                if (data.success) {
                    log('Layout saved', 'success');
                }
            } catch (e) {
                log('Failed to save layout: ' + e, 'error');
            }
        }

        function showNewLayoutDialog() {
            const name = prompt('Enter layout name:');
            if (name) createNewLayout(name);
        }

        async function createNewLayout(name) {
            try {
                const res = await fetch('/api/layout/new', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json();
                if (data.success) {
                    await loadLayout(name);
                    await fetchLayouts();
                    log('Created layout: ' + name, 'success');
                }
            } catch (e) {
                log('Failed to create layout: ' + e, 'error');
            }
        }

        async function deleteCurrentLayout() {
            if (!currentLayout) return;
            if (!confirm('Delete layout "' + currentLayout + '"?')) return;

            try {
                const res = await fetch('/api/layouts/' + currentLayout, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    currentLayout = null;
                    currentLayoutData = null;
                    await fetchLayouts();
                    await fetchStatus();
                    log('Layout deleted', 'success');
                }
            } catch (e) {
                log('Failed to delete layout: ' + e, 'error');
            }
        }

        // Canvas mouse handlers for selection and dragging
        canvas.addEventListener('mousedown', (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = Math.floor((e.clientX - rect.left) / SCALE);
            const y = Math.floor((e.clientY - rect.top) / SCALE);

            // Find widget at position
            let found = null;
            for (const widget of (currentLayoutData?.widgets || []).reverse()) {
                if (hitTest(widget, x, y)) {
                    found = widget;
                    break;
                }
            }

            if (found) {
                selectedWidget = found;
                isDragging = true;
                dragHistorySaved = false;
                dragStartX = x;
                dragStartY = y;
                // Store original position based on widget type
                if (found.type === 'line') {
                    dragWidgetStartX = found.x1 || 0;
                    dragWidgetStartY = found.y1 || 0;
                    dragWidgetStartX2 = found.x2 || 0;
                    dragWidgetStartY2 = found.y2 || 0;
                } else {
                    dragWidgetStartX = found.x || 0;
                    dragWidgetStartY = found.y || 0;
                }
                canvas.style.cursor = 'grabbing';
            } else {
                selectedWidget = null;
            }

            renderCanvas();
            updateWidgetList();
            updatePropertyPanel();
        });

        canvas.addEventListener('mousemove', (e) => {
            if (!isDragging || !selectedWidget) return;

            const rect = canvas.getBoundingClientRect();
            const x = Math.floor((e.clientX - rect.left) / SCALE);
            const y = Math.floor((e.clientY - rect.top) / SCALE);

            const deltaX = x - dragStartX;
            const deltaY = y - dragStartY;

            // Save history on first actual movement
            if (!dragHistorySaved && (deltaX !== 0 || deltaY !== 0)) {
                saveToHistory();
                dragHistorySaved = true;
            }

            // Update widget position based on type
            if (selectedWidget.type === 'line') {
                selectedWidget.x1 = Math.max(0, Math.min(63, dragWidgetStartX + deltaX));
                selectedWidget.y1 = Math.max(0, Math.min(63, dragWidgetStartY + deltaY));
                selectedWidget.x2 = Math.max(0, Math.min(63, dragWidgetStartX2 + deltaX));
                selectedWidget.y2 = Math.max(0, Math.min(63, dragWidgetStartY2 + deltaY));
            } else {
                selectedWidget.x = Math.max(0, Math.min(63, dragWidgetStartX + deltaX));
                selectedWidget.y = Math.max(0, Math.min(63, dragWidgetStartY + deltaY));
            }

            renderCanvas();
        });

        canvas.addEventListener('mouseup', async (e) => {
            if (isDragging && selectedWidget) {
                const rect = canvas.getBoundingClientRect();
                const x = Math.floor((e.clientX - rect.left) / SCALE);
                const y = Math.floor((e.clientY - rect.top) / SCALE);

                // Only save if position actually changed
                if (x !== dragStartX || y !== dragStartY) {
                    // Save the new position to server
                    try {
                        let updates = {};
                        if (selectedWidget.type === 'line') {
                            updates = {
                                x1: selectedWidget.x1,
                                y1: selectedWidget.y1,
                                x2: selectedWidget.x2,
                                y2: selectedWidget.y2
                            };
                        } else {
                            updates = {
                                x: selectedWidget.x,
                                y: selectedWidget.y
                            };
                        }

                        await fetch('/api/layout/widget/' + selectedWidget.id, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ updates })
                        });

                        log('Widget moved', 'success');
                        updatePropertyPanel();
                    } catch (e) {
                        log('Failed to save position: ' + e, 'error');
                    }
                }
            }

            isDragging = false;
            canvas.style.cursor = 'crosshair';
        });

        canvas.addEventListener('mouseleave', () => {
            if (isDragging) {
                isDragging = false;
                canvas.style.cursor = 'crosshair';
                // Reload to discard unsaved drag changes
                loadEditorData();
            }
        });

        function hitTest(widget, x, y) {
            switch (widget.type) {
                case 'text':
                    // Calculate text bounds based on content and font
                    const text = widget.text || widget.data_source || '???';
                    const fontWidth = widget.font === '4x6' ? 4 : 5;
                    const fontHeight = widget.font === '4x6' ? 6 : 7;
                    const textWidth = text.length * (fontWidth + 1);
                    return x >= widget.x && x <= widget.x + textWidth &&
                           y >= widget.y && y <= widget.y + fontHeight;
                case 'rect':
                    return x >= widget.x && x <= widget.x + (widget.width || 10) &&
                           y >= widget.y && y <= widget.y + (widget.height || 10);
                case 'line':
                    // Check if point is within a few pixels of the line bounds
                    const x1 = widget.x1 || 0;
                    const y1 = widget.y1 || 0;
                    const x2 = widget.x2 || 63;
                    const y2 = widget.y2 || 0;
                    const minX = Math.min(x1, x2) - 3;
                    const maxX = Math.max(x1, x2) + 3;
                    const minY = Math.min(y1, y2) - 3;
                    const maxY = Math.max(y1, y2) + 3;
                    // Ensure minimum hit area for horizontal/vertical lines
                    return x >= minX && x <= maxX && y >= minY && y <= maxY;
                case 'clock':
                    const clockText = getClockPreviewText(widget);
                    const clockFontW = widget.font === '4x6' ? 4 : 5;
                    const clockFontH = widget.font === '4x6' ? 6 : 7;
                    const clockWidth = clockText.length * (clockFontW + 1);
                    return x >= widget.x && x <= widget.x + clockWidth &&
                           y >= widget.y && y <= widget.y + clockFontH;
                default:
                    return false;
            }
        }

        // ==================== Data Sources Functions ====================
        async function loadSourcesData() {
            try {
                const res = await fetch('/api/datasources');
                const sources = await res.json();

                let html = '';
                for (const [name, info] of Object.entries(sources)) {
                    const statusClass = info.error ? 'error' : (info.enabled ? 'ok' : 'disabled');
                    const isActive = name === selectedSourceName;
                    html += `<li class="source-item ${isActive ? 'active' : ''}" onclick="selectSource('${name}')">
                             <span>${name} <span style="color:#666">(${info.type})</span></span>
                             <span class="source-status-dot ${statusClass}"></span>
                             </li>`;
                }
                document.getElementById('source-list').innerHTML = html || '<li class="source-item">No sources configured</li>';
            } catch (e) {
                log('Failed to load data sources: ' + e, 'error');
            }
        }

        async function selectSource(name) {
            selectedSourceName = name;
            loadSourcesData();

            try {
                const res = await fetch('/api/datasources/config');
                const config = await res.json();
                const sourceConfig = config.sources[name];

                if (!sourceConfig) {
                    document.getElementById('source-config-content').innerHTML = '<p style="color:#666">Source not found</p>';
                    return;
                }

                let html = `<h3 style="color:#7b2cbf;margin-bottom:15px;">${name} (${sourceConfig.type})</h3>`;

                switch (sourceConfig.type) {
                    case 'stocks':
                        html += `
                            <label>Symbols (comma-separated):
                                <input type="text" id="source-symbols" value="${(sourceConfig.symbols || []).join(', ')}">
                            </label>
                        `;
                        break;
                    case 'weather':
                        html += `
                            <label>API Key:
                                <input type="password" id="source-api-key" value="${sourceConfig.api_key || ''}">
                            </label>
                            <label>Location:
                                <input type="text" id="source-location" value="${sourceConfig.location || ''}">
                            </label>
                            <label>Units:
                                <select id="source-units">
                                    <option value="imperial" ${sourceConfig.units === 'imperial' ? 'selected' : ''}>Imperial (F)</option>
                                    <option value="metric" ${sourceConfig.units === 'metric' ? 'selected' : ''}>Metric (C)</option>
                                </select>
                            </label>
                        `;
                        break;
                    case 'generic':
                        html += `
                            <label>URL:
                                <input type="url" id="source-url" value="${sourceConfig.url || ''}">
                            </label>
                            <label>Method:
                                <select id="source-method">
                                    <option value="GET" ${sourceConfig.method !== 'POST' ? 'selected' : ''}>GET</option>
                                    <option value="POST" ${sourceConfig.method === 'POST' ? 'selected' : ''}>POST</option>
                                </select>
                            </label>
                            <label>JSON Path:
                                <input type="text" id="source-jsonpath" value="${sourceConfig.json_path || ''}" placeholder="$.data.value">
                            </label>
                        `;
                        break;
                }

                html += `
                    <label>Refresh Interval (seconds):
                        <input type="number" id="source-refresh" value="${sourceConfig.refresh_seconds || 300}" min="60">
                    </label>
                    <label>
                        <input type="checkbox" id="source-enabled" ${sourceConfig.enabled !== false ? 'checked' : ''}> Enabled
                    </label>
                    <div class="button-group" style="margin-top:15px;">
                        <button onclick="saveSource('${name}')">Save</button>
                        <button class="secondary" onclick="testSource('${name}')">Test</button>
                        <button class="danger" onclick="deleteSource('${name}')">Delete</button>
                    </div>
                    <div id="test-results" class="test-results" style="display:none;"></div>
                `;

                document.getElementById('source-config-content').innerHTML = html;
            } catch (e) {
                log('Failed to load source config: ' + e, 'error');
            }
        }

        async function saveSource(name) {
            try {
                const res = await fetch('/api/datasources/config');
                const fullConfig = await res.json();
                const config = fullConfig.sources[name];

                // Update config based on type
                config.refresh_seconds = parseInt(document.getElementById('source-refresh').value);
                config.enabled = document.getElementById('source-enabled').checked;

                switch (config.type) {
                    case 'stocks':
                        config.symbols = document.getElementById('source-symbols').value.split(',').map(s => s.trim());
                        break;
                    case 'weather':
                        config.api_key = document.getElementById('source-api-key').value;
                        config.location = document.getElementById('source-location').value;
                        config.units = document.getElementById('source-units').value;
                        break;
                    case 'generic':
                        config.url = document.getElementById('source-url').value;
                        config.method = document.getElementById('source-method').value;
                        config.json_path = document.getElementById('source-jsonpath').value;
                        break;
                }

                const saveRes = await fetch('/api/datasources/' + name, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config })
                });
                const data = await saveRes.json();
                if (data.success) {
                    log('Source saved: ' + name, 'success');
                    loadSourcesData();
                }
            } catch (e) {
                log('Failed to save source: ' + e, 'error');
            }
        }

        async function testSource(name) {
            const resultsEl = document.getElementById('test-results');
            resultsEl.style.display = 'block';
            resultsEl.innerHTML = 'Testing...';

            try {
                const res = await fetch('/api/datasources/' + name + '/test', { method: 'POST' });
                const data = await res.json();

                if (data.success) {
                    resultsEl.innerHTML = '<span style="color:#4ade80">Success!</span><pre>' + JSON.stringify(data.data, null, 2) + '</pre>';
                } else {
                    resultsEl.innerHTML = '<span style="color:#f72585">Error:</span> ' + data.error;
                }
            } catch (e) {
                resultsEl.innerHTML = '<span style="color:#f72585">Error:</span> ' + e;
            }
        }

        async function deleteSource(name) {
            if (!confirm('Delete data source "' + name + '"?')) return;

            try {
                const res = await fetch('/api/datasources/' + name, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    selectedSourceName = null;
                    loadSourcesData();
                    document.getElementById('source-config-content').innerHTML = '<p style="color:#666">Select a data source to configure</p>';
                    log('Source deleted: ' + name, 'success');
                }
            } catch (e) {
                log('Failed to delete source: ' + e, 'error');
            }
        }

        async function addDataSource() {
            const type = document.getElementById('new-source-type').value;
            const name = document.getElementById('new-source-name').value.trim();

            if (!name) {
                log('Please enter a source name', 'error');
                return;
            }

            let config = { type, enabled: true, refresh_seconds: 300 };

            switch (type) {
                case 'stocks':
                    config.symbols = ['AAPL'];
                    break;
                case 'weather':
                    config.api_key = '';
                    config.location = 'New York,US';
                    config.units = 'imperial';
                    config.refresh_seconds = 600;
                    break;
                case 'generic':
                    config.url = 'https://api.example.com/data';
                    config.method = 'GET';
                    break;
            }

            try {
                const res = await fetch('/api/datasources/' + name, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config })
                });
                const data = await res.json();
                if (data.success) {
                    document.getElementById('new-source-name').value = '';
                    loadSourcesData();
                    selectSource(name);
                    log('Created source: ' + name, 'success');
                }
            } catch (e) {
                log('Failed to create source: ' + e, 'error');
            }
        }

        // ==================== Device Functions ====================
        async function loadDeviceInfo() {
            try {
                const res = await fetch('/api/device/ping');
                const ping = await res.json();

                document.getElementById('device-ip').textContent = ping.ip || '-';
                const statusEl = document.getElementById('connection-status');
                if (ping.connected) {
                    statusEl.textContent = 'Connected';
                    statusEl.className = 'status-value success';
                } else {
                    statusEl.textContent = 'Disconnected';
                    statusEl.className = 'status-value error';
                }

                if (ping.connected) {
                    const infoRes = await fetch('/api/device/info');
                    const info = await infoRes.json();

                    let html = '';
                    if (info.info) {
                        for (const [key, value] of Object.entries(info.info)) {
                            html += `<div class="status-item"><span class="status-label">${key}</span>
                                     <span class="status-value">${value}</span></div>`;
                        }
                    }
                    document.getElementById('device-info').innerHTML = html || '<p style="color:#666">No device info available</p>';
                }
            } catch (e) {
                log('Failed to load device info: ' + e, 'error');
            }
        }

        async function togglePower() {
            powerState = !powerState;
            try {
                const res = await fetch('/api/device/power', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ on: powerState })
                });
                const data = await res.json();
                if (data.success) {
                    const btn = document.getElementById('power-btn');
                    btn.textContent = powerState ? 'ON' : 'OFF';
                    btn.className = 'power-btn ' + (powerState ? 'on' : 'off');
                    log('Power: ' + (powerState ? 'ON' : 'OFF'), 'info');
                }
            } catch (e) {
                powerState = !powerState;
                log('Failed to toggle power: ' + e, 'error');
            }
        }

        async function setChannel(channel) {
            try {
                const res = await fetch('/api/device/channel/' + channel, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    document.querySelectorAll('.channel-buttons button').forEach((btn, i) => {
                        btn.classList.toggle('active', i === channel);
                    });
                    const names = ['Clock', 'Cloud', 'VU', 'Custom', 'Off'];
                    log('Channel: ' + names[channel], 'info');
                }
            } catch (e) {
                log('Failed to set channel: ' + e, 'error');
            }
        }

        async function reconnectDevice() {
            try {
                const res = await fetch('/api/device/reconnect', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    log('Reconnected to ' + data.ip, 'success');
                    loadDeviceInfo();
                    fetchStatus();
                }
            } catch (e) {
                log('Failed to reconnect: ' + e, 'error');
            }
        }

        async function pingDevice() {
            try {
                const res = await fetch('/api/device/ping');
                const data = await res.json();
                if (data.connected) {
                    log('Device reachable at ' + data.ip, 'success');
                } else {
                    log('Device not reachable', 'error');
                }
            } catch (e) {
                log('Ping failed: ' + e, 'error');
            }
        }

        async function scanForDevices() {
            const btn = document.getElementById('scan-btn');
            const results = document.getElementById('scan-results');
            btn.disabled = true;
            btn.textContent = 'Scanning...';
            results.innerHTML = '<p style="color:#888">Scanning network...</p>';

            try {
                const res = await fetch('/api/device/scan', { method: 'POST' });
                const data = await res.json();

                if (data.devices && data.devices.length > 0) {
                    let html = '<div style="display: flex; flex-direction: column; gap: 8px;">';
                    for (const ip of data.devices) {
                        html += `<div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; background: #2a2a2a; border-radius: 4px;">
                            <span>${ip}</span>
                            <button onclick="connectToDevice('${ip}')" class="secondary" style="padding: 4px 12px;">Connect</button>
                        </div>`;
                    }
                    html += '</div>';
                    results.innerHTML = html;
                    log('Found ' + data.count + ' device(s)', 'success');
                } else {
                    results.innerHTML = '<p style="color:#888">No devices found</p>';
                    log('No devices found on network', 'info');
                }
            } catch (e) {
                results.innerHTML = '<p style="color:#f55">Scan failed: ' + e + '</p>';
                log('Scan failed: ' + e, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Scan Network';
            }
        }

        async function connectToDevice(ip) {
            try {
                log('Connecting to ' + ip + '...', 'info');
                const res = await fetch('/api/device/connect/' + ip, { method: 'POST' });
                const data = await res.json();

                if (data.success) {
                    log('Connected to ' + ip, 'success');
                    document.getElementById('device-ip').textContent = ip;
                    document.getElementById('connection-status').textContent = 'Connected';
                    document.getElementById('connection-status').className = 'status-value connected';
                    await loadDeviceInfo();
                }
            } catch (e) {
                log('Connection failed: ' + e, 'error');
            }
        }

        // ==================== Quick Actions Functions ====================
        async function loadPresets() {
            try {
                const res = await fetch('/api/quick/presets');
                const presets = await res.json();

                let html = '';
                for (const name of presets) {
                    html += `<button onclick="activatePreset('${name}')">${name}</button>`;
                }
                document.getElementById('preset-grid').innerHTML = html || '<p style="color:#666">No presets</p>';
            } catch (e) {
                log('Failed to load presets: ' + e, 'error');
            }
        }

        async function activatePreset(name) {
            try {
                const res = await fetch('/api/quick/preset/' + name, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    log('Activated preset: ' + name, 'success');
                    currentLayout = name;
                    await refreshPreview();
                }
            } catch (e) {
                log('Failed to activate preset: ' + e, 'error');
            }
        }

        async function sendQuickText() {
            const text = document.getElementById('quick-text').value;
            if (!text) {
                log('Please enter text', 'error');
                return;
            }

            try {
                const res = await fetch('/api/quick/text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text,
                        color: document.getElementById('quick-text-color').value,
                        background: document.getElementById('quick-text-bg').value,
                        font: document.getElementById('quick-text-font').value
                    })
                });
                const data = await res.json();
                if (data.success) {
                    log('Sent text to display', 'success');
                }
            } catch (e) {
                log('Failed to send text: ' + e, 'error');
            }
        }

        function previewQuickImage(input) {
            if (input.files && input.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    document.getElementById('quick-image-preview').innerHTML =
                        '<img src="' + e.target.result + '">';
                };
                reader.readAsDataURL(input.files[0]);
            }
        }

        async function sendQuickImage() {
            const input = document.getElementById('quick-image');
            if (!input.files || !input.files[0]) {
                log('Please select an image', 'error');
                return;
            }

            const formData = new FormData();
            formData.append('file', input.files[0]);

            try {
                const res = await fetch('/api/quick/image', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    log('Sent image to display', 'success');
                }
            } catch (e) {
                log('Failed to send image: ' + e, 'error');
            }
        }

        async function clearDisplay() {
            const color = document.getElementById('clear-color').value;
            try {
                const res = await fetch('/api/quick/text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text: '',
                        background: color
                    })
                });
                const data = await res.json();
                if (data.success) {
                    log('Display cleared', 'success');
                }
            } catch (e) {
                log('Failed to clear display: ' + e, 'error');
            }
        }

        // ==================== Initialization ====================
        async function init() {
            await fetchStatus();
            await fetchData();
            await fetchLayouts();
            await refreshPreview();
            log('Divoom Client ready', 'success');
        }

        // Auto-refresh every 30 seconds
        setInterval(async () => {
            await fetchData();
            await refreshPreview();
        }, 30000);

        init();
    </script>
</body>
</html>'''
