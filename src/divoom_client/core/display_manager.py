"""Display manager for coordinating updates to the Pixoo display."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from divoom_client.core.pixoo import Pixoo
from divoom_client.core.discovery import get_device
from divoom_client.core.frame import Frame
from divoom_client.core.renderer import Renderer
from divoom_client.core.scheduler import Scheduler
from divoom_client.datasources.manager import DataSourceManager
from divoom_client.models.layout import Layout

logger = logging.getLogger(__name__)


class DisplayManager:
    """Manages the complete display pipeline: data -> render -> display."""

    def __init__(
        self,
        config_dir: Path = Path("config"),
        assets_dir: Path = Path("assets"),
    ):
        """Initialize the display manager.

        Args:
            config_dir: Path to configuration directory
            assets_dir: Path to assets directory
        """
        self.config_dir = config_dir
        self.assets_dir = assets_dir

        self._device: Optional[Pixoo] = None
        self._layout: Optional[Layout] = None
        self._data_manager = DataSourceManager()
        self._renderer = Renderer(assets_dir=assets_dir)
        self._scheduler = Scheduler()
        self._current_frame: Optional[Frame] = None
        self._last_data: dict[str, Any] = {}

        # Wire up scheduler callbacks
        self._scheduler.set_data_manager(self._data_manager)
        self._scheduler.set_update_callback(self._on_data_updated)

    @property
    def device(self) -> Optional[Pixoo]:
        """Get the connected Pixoo device."""
        return self._device

    @property
    def layout(self) -> Optional[Layout]:
        """Get the current layout."""
        return self._layout

    @property
    def data(self) -> dict[str, Any]:
        """Get the current data context."""
        return self._last_data

    @property
    def scheduler(self) -> Scheduler:
        """Get the scheduler."""
        return self._scheduler

    def connect(self, ip: Optional[str] = None) -> bool:
        """Connect to a Pixoo device.

        Args:
            ip: Optional IP address (auto-discover if not provided)

        Returns:
            True if connected successfully
        """
        if ip:
            self._device = Pixoo(ip)
            if self._device.ping():
                logger.info(f"Connected to Pixoo at {ip}")
                return True
            else:
                logger.error(f"Could not connect to Pixoo at {ip}")
                self._device = None
                return False
        else:
            self._device = get_device(self.config_dir)
            if self._device:
                logger.info(f"Connected to Pixoo at {self._device.ip_address}")
                return True
            else:
                logger.warning("No Pixoo device found")
                return False

    def load_layout(self, layout_path: Path) -> bool:
        """Load a layout from file.

        Args:
            layout_path: Path to layout JSON file

        Returns:
            True if loaded successfully
        """
        try:
            with open(layout_path) as f:
                layout_data = json.load(f)
            self._layout = Layout.model_validate(layout_data)
            logger.info(f"Loaded layout: {self._layout.name}")
            return True
        except (json.JSONDecodeError, ValueError, FileNotFoundError) as e:
            logger.error(f"Failed to load layout: {e}")
            return False

    def load_datasources(self, datasources_path: Optional[Path] = None) -> bool:
        """Load data sources from configuration.

        Args:
            datasources_path: Path to datasources.json (default: config_dir/datasources.json)

        Returns:
            True if loaded successfully
        """
        path = datasources_path or (self.config_dir / "datasources.json")
        if not path.exists():
            logger.warning(f"Data sources config not found: {path}")
            return False

        try:
            self._data_manager.load_config(path)
            return True
        except Exception as e:
            logger.error(f"Failed to load data sources: {e}")
            return False

    def _on_data_updated(self, data: dict[str, Any]) -> None:
        """Callback when data is updated by scheduler.

        Args:
            data: Updated data context
        """
        self._last_data = data
        logger.debug("Data updated, triggering render")

        # Render and send to device
        if self._layout:
            try:
                self._render_and_send()
            except Exception as e:
                logger.error(f"Render failed: {e}")

    def _render_and_send(self) -> None:
        """Render current layout and send to device."""
        if not self._layout:
            logger.warning("No layout loaded")
            return

        # Render frame
        self._current_frame = self._renderer.render(self._layout, self._last_data)

        # Send to device if connected
        if self._device:
            try:
                self._device.send_pixels(self._current_frame.to_pixels())
                logger.debug("Frame sent to device")
            except Exception as e:
                logger.error(f"Failed to send frame to device: {e}")

    def render(self) -> Optional[Frame]:
        """Render the current layout with current data.

        Returns:
            Rendered frame or None
        """
        if not self._layout:
            return None

        self._current_frame = self._renderer.render(self._layout, self._last_data)
        return self._current_frame

    def send_to_device(self) -> bool:
        """Send current frame to device.

        Returns:
            True if sent successfully
        """
        if not self._current_frame:
            logger.warning("No frame to send")
            return False

        if not self._device:
            logger.warning("No device connected")
            return False

        try:
            self._device.send_pixels(self._current_frame.to_pixels())
            return True
        except Exception as e:
            logger.error(f"Failed to send frame: {e}")
            return False

    async def start(self) -> None:
        """Start the display manager with scheduled updates."""
        logger.info("Starting display manager...")

        # Start scheduler (will do initial data fetch)
        await self._scheduler.start()

        # Add layout refresh job if layout has refresh_seconds
        if self._layout and self._layout.refresh_seconds:
            self._scheduler.add_job(
                self._render_and_send,
                interval_seconds=self._layout.refresh_seconds,
                job_id="layout_refresh",
                name=f"Refresh display every {self._layout.refresh_seconds}s",
            )

        logger.info("Display manager started")

    def stop(self) -> None:
        """Stop the display manager."""
        self._scheduler.stop()
        logger.info("Display manager stopped")

    async def run_forever(self) -> None:
        """Run the display manager continuously."""
        await self.start()

        try:
            # Keep running until interrupted
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Display manager interrupted")
        finally:
            self.stop()

    def get_status(self) -> dict[str, Any]:
        """Get current status of the display manager.

        Returns:
            Status dictionary
        """
        return {
            "device_connected": self._device is not None,
            "device_ip": self._device.ip_address if self._device else None,
            "layout_loaded": self._layout is not None,
            "layout_name": self._layout.name if self._layout else None,
            "data_sources": list(self._data_manager.sources.keys()),
            "scheduler_running": self._scheduler.is_running,
            "scheduled_jobs": self._scheduler.get_jobs(),
        }
