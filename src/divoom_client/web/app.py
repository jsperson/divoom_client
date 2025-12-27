"""FastAPI web application for Divoom Client."""

import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RefreshRequest(BaseModel):
    """Request to refresh data sources."""
    source: Optional[str] = None  # None means refresh all


class LayoutUpdate(BaseModel):
    """Request to update layout."""
    layout: dict[str, Any]


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

    # Store display manager reference
    app.state.display_manager = display_manager

    # --- API Routes ---

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
                # Trigger re-render
                display_manager._last_data = data
                display_manager._render_and_send()
                return {"success": True, "data": data}
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/layout")
    async def get_layout() -> dict[str, Any]:
        """Get current layout configuration."""
        if not display_manager.layout:
            raise HTTPException(status_code=404, detail="No layout loaded")
        return display_manager.layout.model_dump()

    @app.get("/api/layouts")
    async def list_layouts() -> list[str]:
        """List available layouts."""
        layouts_dir = display_manager.config_dir / "layouts"
        if not layouts_dir.exists():
            return []
        return [p.stem for p in layouts_dir.glob("*.json")]

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
        with open(layout_path, "w") as f:
            json.dump(update.layout, f, indent=2)
        return {"success": True, "path": str(layout_path)}

    @app.post("/api/layout/load/{name}")
    async def load_layout(name: str) -> dict[str, Any]:
        """Load and activate a layout."""
        layout_path = display_manager.config_dir / "layouts" / f"{name}.json"
        if not display_manager.load_layout(layout_path):
            raise HTTPException(status_code=400, detail=f"Failed to load layout: {name}")
        # Re-render with new layout
        display_manager._render_and_send()
        return {"success": True, "layout": name}

    @app.get("/api/preview")
    async def get_preview() -> Response:
        """Get current frame as PNG image."""
        frame = display_manager.render()
        if not frame:
            raise HTTPException(status_code=404, detail="No frame available")

        # Convert to PNG
        img = frame.to_image()
        # Scale up for visibility
        img = img.resize((256, 256), resample=0)  # Nearest neighbor

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

    # --- Web UI ---

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the main web UI."""
        return get_index_html()

    return app


def get_index_html() -> str:
    """Return the main HTML page."""
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
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #4cc9f0; margin-bottom: 20px; }
        h2 { color: #7b2cbf; margin: 20px 0 10px; font-size: 1.2em; }

        .grid { display: grid; grid-template-columns: 300px 1fr; gap: 20px; }

        .card {
            background: #16213e;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }

        .preview-container {
            text-align: center;
            padding: 20px;
        }
        .preview {
            image-rendering: pixelated;
            border: 2px solid #333;
            background: #000;
        }

        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #333;
        }
        .status-item:last-child { border-bottom: none; }
        .status-label { color: #888; }
        .status-value { color: #4cc9f0; }
        .status-value.error { color: #f72585; }
        .status-value.success { color: #4ade80; }

        .data-section { margin-top: 10px; }
        .data-source {
            background: #0f0f23;
            border-radius: 4px;
            padding: 10px;
            margin: 5px 0;
            font-family: monospace;
            font-size: 12px;
        }
        .data-source h4 {
            color: #7b2cbf;
            margin-bottom: 5px;
        }

        button {
            background: #4cc9f0;
            color: #000;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            margin: 5px;
        }
        button:hover { background: #7b2cbf; color: #fff; }
        button:disabled { background: #333; color: #666; cursor: not-allowed; }

        .button-group { margin: 15px 0; }

        .layout-list {
            list-style: none;
        }
        .layout-list li {
            padding: 8px;
            margin: 5px 0;
            background: #0f0f23;
            border-radius: 4px;
            cursor: pointer;
        }
        .layout-list li:hover { background: #1a1a4e; }
        .layout-list li.active { border-left: 3px solid #4cc9f0; }

        .refresh-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #4ade80;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        #log {
            background: #0f0f23;
            padding: 10px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 11px;
            max-height: 150px;
            overflow-y: auto;
        }
        .log-entry { padding: 2px 0; color: #888; }
        .log-entry.info { color: #4cc9f0; }
        .log-entry.error { color: #f72585; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Divoom Client</h1>

        <div class="grid">
            <div class="sidebar">
                <div class="card preview-container">
                    <img id="preview" class="preview" width="256" height="256" alt="Display Preview">
                    <div class="button-group">
                        <button onclick="refreshPreview()">Refresh</button>
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
                    <div style="margin-top: 10px;">
                        <label>Brightness: <span id="brightness-value">100</span>%</label><br>
                        <input type="range" id="brightness" min="0" max="100" value="100"
                               onchange="setBrightness(this.value)" style="width: 100%; margin-top: 5px;">
                    </div>
                </div>
            </div>

            <div class="main">
                <div class="card">
                    <h2><span class="refresh-indicator"></span>Live Data</h2>
                    <div id="data" class="data-section">Loading...</div>
                </div>

                <div class="card">
                    <h2>Log</h2>
                    <div id="log"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentLayout = null;

        function log(message, type = '') {
            const logEl = document.getElementById('log');
            const entry = document.createElement('div');
            entry.className = 'log-entry ' + type;
            entry.textContent = new Date().toLocaleTimeString() + ' - ' + message;
            logEl.insertBefore(entry, logEl.firstChild);
            if (logEl.children.length > 50) logEl.removeChild(logEl.lastChild);
        }

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
                document.getElementById('data').innerHTML = html || '<p>No data</p>';
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
                    html += `<li class="${isActive ? 'active' : ''}"
                             onclick="loadLayout('${name}')">${name}</li>`;
                }
                document.getElementById('layouts').innerHTML = html || '<li>No layouts</li>';
            } catch (e) {
                log('Failed to fetch layouts: ' + e, 'error');
            }
        }

        async function refreshPreview() {
            try {
                const res = await fetch('/api/preview/base64');
                const data = await res.json();
                document.getElementById('preview').src = data.image;
                log('Preview refreshed', 'info');
            } catch (e) {
                log('Failed to refresh preview: ' + e, 'error');
            }
        }

        async function sendToDevice() {
            try {
                const res = await fetch('/api/send', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    log('Sent to device', 'info');
                }
            } catch (e) {
                log('Failed to send to device: ' + e, 'error');
            }
        }

        async function refreshAllData() {
            try {
                log('Refreshing all data...', 'info');
                const res = await fetch('/api/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await res.json();
                if (data.success) {
                    log('Data refreshed', 'info');
                    await fetchData();
                    await refreshPreview();
                }
            } catch (e) {
                log('Failed to refresh data: ' + e, 'error');
            }
        }

        async function loadLayout(name) {
            try {
                const res = await fetch('/api/layout/load/' + name, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    log('Loaded layout: ' + name, 'info');
                    currentLayout = name;
                    await fetchLayouts();
                    await refreshPreview();
                }
            } catch (e) {
                log('Failed to load layout: ' + e, 'error');
            }
        }

        async function setBrightness(value) {
            document.getElementById('brightness-value').textContent = value;
            try {
                await fetch('/api/brightness/' + value, { method: 'POST' });
                log('Brightness set to ' + value + '%', 'info');
            } catch (e) {
                log('Failed to set brightness: ' + e, 'error');
            }
        }

        // Initial load
        async function init() {
            await fetchStatus();
            await fetchData();
            await fetchLayouts();
            await refreshPreview();
            log('Divoom Client ready', 'info');
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
