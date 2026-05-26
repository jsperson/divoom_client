"""FastAPI web application for Divoom Client."""

import base64
import io
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# --- Request Models ---

class RefreshRequest(BaseModel):
    """Request to refresh data sources."""
    source: str | None = None


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
                try:
                    source = display_manager._data_manager.get_source(request.source)
                    data = await display_manager._data_manager.refresh(request.source)
                    response = {
                        "success": True,
                        "source": request.source,
                        "data": data,
                        "errors": {},
                    }
                    service_response = getattr(source, "last_service_response", None)
                    if service_response is not None:
                        response["service_response"] = service_response
                    return response
                except Exception as e:
                    source = display_manager._data_manager.get_source(request.source)
                    response = {
                        "success": False,
                        "source": request.source,
                        "data": {},
                        "errors": {request.source: str(e)},
                    }
                    service_response = getattr(source, "last_service_response", None)
                    if service_response is not None:
                        response["service_response"] = service_response
                    return response
            else:
                data = await display_manager._data_manager.refresh_all()
                errors = {
                    name: source.last_error
                    for name, source in display_manager._data_manager.sources.items()
                    if source.last_error
                }
                display_manager._last_data = data
                display_manager._render_and_send()
                return {"success": not errors, "data": data, "errors": errors}
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

    def safe_layout_name(name: str) -> str:
        """Return a filesystem-safe layout name or raise HTTP 400."""
        cleaned = name.strip()
        if not cleaned or cleaned in {".", ".."}:
            raise HTTPException(status_code=400, detail="Layout name is required")
        if "/" in cleaned or "\\" in cleaned:
            raise HTTPException(status_code=400, detail="Layout name cannot contain slashes")
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,63}$", cleaned):
            raise HTTPException(
                status_code=400,
                detail="Use letters, numbers, spaces, dots, underscores, or hyphens",
            )
        return cleaned

    def layout_file(name: str) -> Path:
        """Resolve a safe layout path under config/layouts."""
        return display_manager.config_dir / "layouts" / f"{safe_layout_name(name)}.json"

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

    @app.get("/api/layouts/meta")
    async def list_layouts_meta() -> list[dict[str, Any]]:
        """List available layouts with lightweight metadata."""
        layouts_dir = display_manager.config_dir / "layouts"
        if not layouts_dir.exists():
            return []
        active_name = display_manager.layout.name if display_manager.layout else None
        layouts: list[dict[str, Any]] = []
        for path in sorted(layouts_dir.glob("*.json"), key=lambda p: p.stem.lower()):
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                data = {}
            stat = path.stat()
            layouts.append(
                {
                    "name": path.stem,
                    "active": path.stem == active_name,
                    "widgets": len(data.get("widgets", [])),
                    "refresh_seconds": data.get("refresh_seconds"),
                    "modified": stat.st_mtime,
                }
            )
        return layouts

    @app.get("/api/layouts/{name}")
    async def get_layout_by_name(name: str) -> dict[str, Any]:
        """Get a specific layout by name."""
        layout_path = layout_file(name)
        if not layout_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        with open(layout_path) as f:
            return json.load(f)

    @app.post("/api/layouts/{name}")
    async def save_layout(name: str, update: LayoutUpdate) -> dict[str, Any]:
        """Save a layout."""
        layouts_dir = display_manager.config_dir / "layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_layout_name(name)
        layout_path = layouts_dir / f"{safe_name}.json"
        layout = dict(update.layout)
        layout["name"] = safe_name
        # Atomic write
        temp_path = layout_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(layout, f, indent=2)
        temp_path.rename(layout_path)
        if display_manager.layout and display_manager.layout.name == safe_name:
            display_manager.load_layout(layout_path)
        return {"success": True, "path": str(layout_path)}

    @app.delete("/api/layouts/{name}")
    async def delete_layout(name: str) -> dict[str, Any]:
        """Delete a layout."""
        layout_path = layout_file(name)
        if not layout_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        layout_path.unlink()
        return {"success": True}

    @app.post("/api/layouts/{name}/duplicate/{new_name}")
    async def duplicate_layout(name: str, new_name: str) -> dict[str, Any]:
        """Duplicate a layout under a new name."""
        source_path = layout_file(name)
        target_name = safe_layout_name(new_name)
        target_path = layout_file(target_name)
        if not source_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        if target_path.exists():
            raise HTTPException(status_code=400, detail=f"Layout already exists: {target_name}")
        with open(source_path) as f:
            data = json.load(f)
        data["name"] = target_name
        with open(target_path, "w") as f:
            json.dump(data, f, indent=2)
        return {"success": True, "layout": target_name}

    @app.post("/api/layouts/{name}/rename/{new_name}")
    async def rename_layout(name: str, new_name: str) -> dict[str, Any]:
        """Rename a layout file and internal layout name."""
        source_path = layout_file(name)
        target_name = safe_layout_name(new_name)
        target_path = layout_file(target_name)
        if not source_path.exists():
            raise HTTPException(status_code=404, detail=f"Layout not found: {name}")
        if target_path.exists():
            raise HTTPException(status_code=400, detail=f"Layout already exists: {target_name}")
        with open(source_path) as f:
            data = json.load(f)
        data["name"] = target_name
        with open(target_path, "w") as f:
            json.dump(data, f, indent=2)
        source_path.unlink()
        if display_manager.layout and display_manager.layout.name == name:
            display_manager.load_layout(target_path)
        return {"success": True, "layout": target_name}

    @app.post("/api/layouts/import")
    async def import_layout(file: UploadFile = File(...)) -> dict[str, Any]:
        """Import a layout JSON file."""
        try:
            contents = await file.read()
            data = json.loads(contents.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
        name = safe_layout_name(str(data.get("name") or Path(file.filename or "layout").stem))
        path = layout_file(name)
        if path.exists():
            raise HTTPException(status_code=400, detail=f"Layout already exists: {name}")
        data["name"] = name
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return {"success": True, "layout": name}

    @app.post("/api/layout/load/{name}")
    async def load_layout(name: str) -> dict[str, Any]:
        """Load and activate a layout."""
        layout_path = layout_file(name)
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
        """Attempt to reconnect to the configured device."""
        try:
            connected = display_manager.connect()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

        if not connected:
            raise HTTPException(status_code=400, detail="Could not reconnect to device")

        status = display_manager.get_status()
        return {"success": True, "ip": status.get("device_ip")}

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
            connected = display_manager.connect(ip)
            if not connected:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not connect to device at {ip}",
                )
            config = DeviceConfig(ip_address=ip)
            config_path = display_manager.config_dir / "device.json"
            save_device_config(config, config_path)
            return {"success": True, "ip": ip}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # --- Data Source APIs ---

    def reload_datasources_from_config() -> None:
        """Reload data sources and reschedule refresh jobs after config edits."""
        display_manager.load_datasources(display_manager.config_dir / "datasources.json")
        if hasattr(display_manager.scheduler, "reschedule_data_sources"):
            display_manager.scheduler.reschedule_data_sources()

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

        # Register with manager if enabled, and update scheduled refresh jobs.
        try:
            reload_datasources_from_config()
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

        try:
            reload_datasources_from_config()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Best-effort refresh so edits show immediately. Save still succeeds if the
        # remote service/API key is currently bad; the Test button reports details.
        if request.config.get("enabled", True):
            try:
                data = await display_manager._data_manager.refresh(name)
                display_manager._last_data[name] = data
                display_manager._render_and_send()
            except Exception as e:
                logger.warning(f"Saved data source '{name}', but refresh failed: {e}")

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

        # Unregister from manager and scheduler
        reload_datasources_from_config()
        display_manager._last_data.pop(name, None)

        return {"success": True}

    @app.post("/api/datasources/{name}/test")
    async def test_datasource(name: str) -> dict[str, Any]:
        """Test a data source and return fetched data."""
        source = display_manager._data_manager.get_source(name)
        if not source:
            raise HTTPException(status_code=404, detail=f"Data source not found: {name}")

        try:
            data = await source.refresh()
            response = {"success": True, "data": data}
            service_response = getattr(source, "last_service_response", None)
            if service_response is not None:
                response["service_response"] = service_response
            return response
        except Exception as e:
            response = {"success": False, "error": str(e)}
            service_response = getattr(source, "last_service_response", None)
            if service_response is not None:
                response["service_response"] = service_response
            return response

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

        enabled = config["sources"][name]["enabled"]
        reload_datasources_from_config()
        response: dict[str, Any] = {"success": True, "enabled": enabled}
        if enabled:
            try:
                data = await display_manager._data_manager.refresh(name)
                display_manager._last_data[name] = data
                response["data"] = data
            except Exception as e:
                response["success"] = False
                response["error"] = str(e)
                response["errors"] = {name: str(e)}
                source = display_manager._data_manager.get_source(name)
                service_response = getattr(source, "last_service_response", None)
                if service_response is not None:
                    response["service_response"] = service_response
        else:
            display_manager._last_data.pop(name, None)
        display_manager._render_and_send()

        return response

    # --- Quick Action APIs ---

    @app.post("/api/quick/text")
    async def quick_text(request: QuickTextRequest) -> dict[str, Any]:
        """Send text directly to the display."""
        from divoom_client.core.fonts import get_font
        from divoom_client.core.frame import Frame
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
    """Return the main HTML page."""
    static_path = Path(__file__).parent / "static" / "index.html"
    return static_path.read_text(encoding="utf-8")
