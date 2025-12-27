"""CLI for divoom_client."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from divoom_client import __version__
from divoom_client.core.discovery import discover_device, get_device, scan_network
from divoom_client.core.frame import Frame
from divoom_client.core.pixoo import Pixoo
from divoom_client.core.renderer import Renderer
from divoom_client.models.layout import Layout

app = typer.Typer(
    name="divoom",
    help="Divoom Pixoo 64 display manager",
    no_args_is_help=True,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


@app.callback()
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
) -> None:
    """Divoom Pixoo 64 display manager."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@app.command()
def version() -> None:
    """Show version information."""
    typer.echo(f"divoom-client v{__version__}")


@app.command()
def discover(
    config_dir: Path = typer.Option(
        Path("config"),
        "--config-dir", "-c",
        help="Path to config directory",
    ),
) -> None:
    """Discover Pixoo devices on the network."""
    typer.echo("Scanning network for Pixoo devices...")

    devices = scan_network()

    if devices:
        typer.echo(f"\nFound {len(devices)} device(s):")
        for ip in devices:
            typer.echo(f"  - {ip}")
    else:
        typer.echo("\nNo devices found.")
        typer.echo("Make sure your Pixoo is powered on and connected to the same network.")


@app.command()
def test(
    ip: Optional[str] = typer.Option(
        None,
        "--ip",
        help="IP address of Pixoo device (auto-discover if not specified)",
    ),
    config_dir: Path = typer.Option(
        Path("config"),
        "--config-dir", "-c",
        help="Path to config directory",
    ),
) -> None:
    """Test connection to Pixoo device."""
    if ip:
        typer.echo(f"Testing connection to {ip}...")
        device = Pixoo(ip)
    else:
        typer.echo("Discovering device...")
        device = get_device(config_dir)

    if device is None:
        typer.echo("No device found.", err=True)
        raise typer.Exit(1)

    if device.ping():
        typer.echo(f"Successfully connected to Pixoo at {device.ip_address}")

        try:
            info = device.get_device_info()
            typer.echo(f"  Device ID: {info.get('DeviceId', 'unknown')}")
            typer.echo(f"  Brightness: {info.get('Brightness', 'unknown')}%")
        except Exception as e:
            typer.echo(f"  (Could not get device info: {e})")
    else:
        typer.echo(f"Failed to connect to {device.ip_address}", err=True)
        raise typer.Exit(1)


@app.command()
def brightness(
    level: int = typer.Argument(..., min=0, max=100, help="Brightness level (0-100)"),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Set display brightness."""
    if ip:
        device = Pixoo(ip)
    else:
        device = get_device(config_dir)

    if device is None:
        typer.echo("No device found.", err=True)
        raise typer.Exit(1)

    device.set_brightness(level)
    typer.echo(f"Brightness set to {level}%")


@app.command()
def clear(
    color: str = typer.Option("#000000", "--color", help="Fill color (hex)"),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Clear the display with a solid color."""
    if ip:
        device = Pixoo(ip)
    else:
        device = get_device(config_dir)

    if device is None:
        typer.echo("No device found.", err=True)
        raise typer.Exit(1)

    color = color.lstrip("#")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)

    device.clear((r, g, b))
    typer.echo(f"Display cleared with color #{color}")


@app.command()
def on(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Turn display on."""
    if ip:
        device = Pixoo(ip)
    else:
        device = get_device(config_dir)

    if device is None:
        typer.echo("No device found.", err=True)
        raise typer.Exit(1)

    device.set_screen_on(True)
    typer.echo("Display turned on")


@app.command()
def off(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Turn display off."""
    if ip:
        device = Pixoo(ip)
    else:
        device = get_device(config_dir)

    if device is None:
        typer.echo("No device found.", err=True)
        raise typer.Exit(1)

    device.set_screen_on(False)
    typer.echo("Display turned off")


@app.command()
def render(
    layout_file: Path = typer.Argument(..., help="Path to layout JSON file"),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Save rendered frame to image file instead of sending to device",
    ),
    data_file: Optional[Path] = typer.Option(
        None,
        "--data", "-d",
        help="JSON file with data context for dynamic content",
    ),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
    assets_dir: Path = typer.Option(Path("assets"), "--assets", "-a", help="Assets directory"),
) -> None:
    """Render a layout and send to device or save as image."""
    # Load layout
    if not layout_file.exists():
        typer.echo(f"Layout file not found: {layout_file}", err=True)
        raise typer.Exit(1)

    try:
        with open(layout_file) as f:
            layout_data = json.load(f)
        layout = Layout.model_validate(layout_data)
    except (json.JSONDecodeError, ValueError) as e:
        typer.echo(f"Invalid layout file: {e}", err=True)
        raise typer.Exit(1)

    # Load data context if provided
    data: dict = {}
    if data_file:
        if not data_file.exists():
            typer.echo(f"Data file not found: {data_file}", err=True)
            raise typer.Exit(1)
        try:
            with open(data_file) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            typer.echo(f"Invalid data file: {e}", err=True)
            raise typer.Exit(1)

    # Render layout
    renderer = Renderer(assets_dir=assets_dir)
    typer.echo(f"Rendering layout: {layout.name}")
    frame = renderer.render(layout, data)

    # Output
    if output:
        frame.save(str(output))
        typer.echo(f"Saved to {output}")
    else:
        # Send to device
        if ip:
            device = Pixoo(ip)
        else:
            device = get_device(config_dir)

        if device is None:
            typer.echo("No device found. Use --output to save as image instead.", err=True)
            raise typer.Exit(1)

        device.send_pixels(frame.to_pixels())
        typer.echo(f"Sent to device at {device.ip_address}")


@app.command()
def live(
    layout_file: Path = typer.Argument(..., help="Path to layout JSON file"),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Save rendered frame to image file instead of sending to device",
    ),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
    assets_dir: Path = typer.Option(Path("assets"), "--assets", "-a", help="Assets directory"),
) -> None:
    """Fetch live data and render a layout."""
    import asyncio
    from divoom_client.datasources.manager import DataSourceManager

    # Load layout
    if not layout_file.exists():
        typer.echo(f"Layout file not found: {layout_file}", err=True)
        raise typer.Exit(1)

    try:
        with open(layout_file) as f:
            layout_data = json.load(f)
        layout = Layout.model_validate(layout_data)
    except (json.JSONDecodeError, ValueError) as e:
        typer.echo(f"Invalid layout file: {e}", err=True)
        raise typer.Exit(1)

    # Load data sources
    manager = DataSourceManager()
    datasources_config = config_dir / "datasources.json"

    if datasources_config.exists():
        manager.load_config(datasources_config)

    # Fetch data
    if manager.sources:
        typer.echo(f"Fetching data from {len(manager.sources)} source(s)...")
        data = asyncio.run(manager.refresh_all())
    else:
        typer.echo("No data sources configured, using empty data context")
        data = {}

    # Render layout
    renderer = Renderer(assets_dir=assets_dir)
    typer.echo(f"Rendering layout: {layout.name}")
    frame = renderer.render(layout, data)

    # Output
    if output:
        frame.save(str(output))
        typer.echo(f"Saved to {output}")
    else:
        # Send to device
        if ip:
            device = Pixoo(ip)
        else:
            device = get_device(config_dir)

        if device is None:
            typer.echo("No device found. Use --output to save as image instead.", err=True)
            raise typer.Exit(1)

        device.send_pixels(frame.to_pixels())
        typer.echo(f"Sent to device at {device.ip_address}")


@app.command()
def fetch(
    source: Optional[str] = typer.Argument(
        None,
        help="Specific data source to fetch (or 'all' for all sources)",
    ),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Save fetched data to JSON file",
    ),
) -> None:
    """Fetch data from configured data sources."""
    import asyncio
    from divoom_client.datasources.manager import DataSourceManager

    manager = DataSourceManager()
    datasources_config = config_dir / "datasources.json"

    if not datasources_config.exists():
        typer.echo(f"Data sources config not found: {datasources_config}", err=True)
        typer.echo("Create config/datasources.json to configure data sources.")
        raise typer.Exit(1)

    manager.load_config(datasources_config)

    if not manager.sources:
        typer.echo("No data sources configured.", err=True)
        raise typer.Exit(1)

    async def do_fetch() -> dict:
        if source and source != "all":
            if source not in manager.sources:
                typer.echo(f"Unknown data source: {source}", err=True)
                typer.echo(f"Available sources: {list(manager.sources.keys())}")
                raise typer.Exit(1)
            data = await manager.refresh(source)
            return {source: data}
        else:
            return await manager.refresh_all()

    typer.echo(f"Fetching data from {len(manager.sources)} source(s)...")
    data = asyncio.run(do_fetch())

    if output:
        with open(output, "w") as f:
            json.dump(data, f, indent=2, default=str)
        typer.echo(f"Data saved to {output}")
    else:
        typer.echo("\nFetched data:")
        typer.echo(json.dumps(data, indent=2, default=str))


@app.command()
def demo(
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Save demo frame to image file instead of sending to device",
    ),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Render a demo frame to test the display."""
    from divoom_client.core.fonts import get_font

    frame = Frame("#000033")

    # Draw some shapes
    frame.draw_rect(0, 0, 64, 10, (0, 50, 100), filled=True)  # Header bar
    frame.draw_line(0, 10, 63, 10, (100, 100, 100))  # Separator

    # Draw text
    font = get_font("5x7")
    text = "DIVOOM"
    x = 14
    for char in text:
        pixels = font.render_char(char, (255, 255, 255))
        for px, py, color in pixels:
            frame.set_pixel(x + px, 2 + py, color)
        x += font.width + font.spacing

    # Draw some colored rectangles
    frame.draw_rect(5, 20, 15, 15, (255, 0, 0), filled=True)   # Red
    frame.draw_rect(25, 20, 15, 15, (0, 255, 0), filled=True)  # Green
    frame.draw_rect(45, 20, 15, 15, (0, 0, 255), filled=True)  # Blue

    # Draw stock-like text
    x = 5
    for char in "$123.45":
        pixels = font.render_char(char, (0, 255, 100))
        for px, py, color in pixels:
            frame.set_pixel(x + px, 45 + py, color)
        x += font.width + font.spacing

    # Output
    if output:
        frame.save(str(output))
        typer.echo(f"Demo frame saved to {output}")
    else:
        if ip:
            device = Pixoo(ip)
        else:
            device = get_device(config_dir)

        if device is None:
            typer.echo("No device found. Use --output to save as image instead.", err=True)
            raise typer.Exit(1)

        device.send_pixels(frame.to_pixels())
        typer.echo(f"Demo frame sent to device at {device.ip_address}")


@app.command()
def serve(
    layout_file: Path = typer.Argument(..., help="Path to layout JSON file"),
    ip: Optional[str] = typer.Option(None, "--ip", help="IP address of Pixoo device"),
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
    assets_dir: Path = typer.Option(Path("assets"), "--assets", "-a", help="Assets directory"),
    web: bool = typer.Option(False, "--web", "-w", help="Start web UI"),
    web_port: int = typer.Option(8080, "--port", "-p", help="Web UI port"),
    no_device: bool = typer.Option(False, "--no-device", help="Run without connecting to device"),
) -> None:
    """Start the display manager with scheduled updates.

    This runs continuously, fetching data and updating the display
    according to the configured refresh intervals.
    """
    import asyncio
    import signal
    from divoom_client.core.display_manager import DisplayManager

    manager = DisplayManager(config_dir=config_dir, assets_dir=assets_dir)

    # Load layout
    if not manager.load_layout(layout_file):
        typer.echo(f"Failed to load layout: {layout_file}", err=True)
        raise typer.Exit(1)

    # Load data sources
    manager.load_datasources()

    # Connect to device (unless --no-device)
    if not no_device:
        if not manager.connect(ip):
            typer.echo("Warning: No device connected. Display updates will be skipped.", err=True)
            typer.echo("Use --no-device to suppress this warning.")

    typer.echo(f"Starting display manager with layout: {manager.layout.name}")
    typer.echo(f"Data sources: {list(manager._data_manager.sources.keys())}")
    typer.echo("Press Ctrl+C to stop\n")

    async def run() -> None:
        # Handle signals for graceful shutdown
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        def handle_signal() -> None:
            typer.echo("\nShutting down...")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

        # Start the manager
        await manager.start()

        # Show status
        status = manager.get_status()
        typer.echo("Status:")
        typer.echo(f"  Device: {status['device_ip'] or 'not connected'}")
        typer.echo(f"  Layout: {status['layout_name']}")
        typer.echo(f"  Data sources: {', '.join(status['data_sources']) or 'none'}")
        typer.echo(f"  Scheduled jobs: {len(status['scheduled_jobs'])}")
        typer.echo("")

        # If web UI requested, start it
        if web:
            import uvicorn
            from divoom_client.web.app import create_app

            web_app = create_app(manager)
            config = uvicorn.Config(
                web_app,
                host="0.0.0.0",
                port=web_port,
                log_level="info",
            )
            server = uvicorn.Server(config)

            typer.echo(f"Web UI available at http://localhost:{web_port}")
            typer.echo("")

            # Run server until stop signal
            server_task = asyncio.create_task(server.serve())
            await stop_event.wait()
            server.should_exit = True
            await server_task
        else:
            # Wait for stop signal
            await stop_event.wait()

        manager.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass

    typer.echo("Display manager stopped.")


@app.command()
def status(
    config_dir: Path = typer.Option(Path("config"), "--config-dir", "-c"),
) -> None:
    """Show status of data sources and configuration."""
    from divoom_client.datasources.manager import DataSourceManager

    # Check device config
    device_config = config_dir / "device.json"
    if device_config.exists():
        with open(device_config) as f:
            device = json.load(f)
        typer.echo("Device configuration:")
        typer.echo(f"  IP: {device.get('ip_address') or 'auto-discover'}")
        typer.echo(f"  Brightness: {device.get('brightness', 100)}%")
    else:
        typer.echo("Device configuration: not found")

    typer.echo("")

    # Check data sources
    datasources_config = config_dir / "datasources.json"
    if datasources_config.exists():
        manager = DataSourceManager()
        manager.load_config(datasources_config)
        typer.echo(f"Data sources ({len(manager.sources)}):")
        for name, source in manager.sources.items():
            typer.echo(f"  {name}: {source.source_type} (every {source.config.refresh_seconds}s)")
    else:
        typer.echo("Data sources: not configured")

    typer.echo("")

    # Check layouts
    layouts_dir = config_dir / "layouts"
    if layouts_dir.exists():
        layouts = list(layouts_dir.glob("*.json"))
        typer.echo(f"Layouts ({len(layouts)}):")
        for layout_path in layouts:
            typer.echo(f"  {layout_path.name}")
    else:
        typer.echo("Layouts: none found")


if __name__ == "__main__":
    app()
