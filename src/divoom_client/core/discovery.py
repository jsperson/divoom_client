"""Device discovery for Pixoo devices."""

import concurrent.futures
import json
import logging
import socket
from pathlib import Path
from typing import Optional

import requests

from divoom_client.core.pixoo import Pixoo
from divoom_client.models.config import DeviceConfig

logger = logging.getLogger(__name__)

PIXOO_DISCOVERY_PORT = 8888
DISCOVERY_TIMEOUT = 3.0
DISCOVERY_MESSAGE = b"divoom"
HTTP_SCAN_TIMEOUT = 0.5
HTTP_SCAN_WORKERS = 50


def load_device_config(config_path: Path) -> Optional[DeviceConfig]:
    """Load device configuration from file.

    Args:
        config_path: Path to device.json

    Returns:
        DeviceConfig if file exists and is valid, None otherwise
    """
    if not config_path.exists():
        logger.debug(f"No config file at {config_path}")
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)
        config = DeviceConfig.model_validate(data)
        logger.debug(f"Loaded device config: {config}")
        return config
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Invalid device config at {config_path}: {e}")
        return None


def save_device_config(config: DeviceConfig, config_path: Path) -> None:
    """Save device configuration to file.

    Args:
        config: Device configuration to save
        config_path: Path to save to
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config.model_dump(exclude_none=True), f, indent=2)
    logger.info(f"Saved device config to {config_path}")


def _check_pixoo_http(ip: str) -> Optional[str]:
    """Check if an IP has a Pixoo device via HTTP API.

    Args:
        ip: IP address to check

    Returns:
        IP address if Pixoo found, None otherwise
    """
    try:
        r = requests.post(
            f"http://{ip}:80/post",
            json={"Command": "Channel/GetIndex"},
            timeout=HTTP_SCAN_TIMEOUT,
        )
        if r.status_code == 200 and "error_code" in r.json():
            return ip
    except Exception:
        pass
    return None


def _get_local_subnet() -> Optional[str]:
    """Get the local subnet prefix (e.g., '192.168.1').

    Returns:
        Subnet prefix or None if unable to determine
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return ".".join(local_ip.split(".")[:3])
    except Exception:
        return None


def scan_network_http() -> list[str]:
    """Scan the local subnet for Pixoo devices using HTTP.

    This is slower but more reliable than UDP broadcast.

    Returns:
        List of discovered IP addresses
    """
    subnet = _get_local_subnet()
    if not subnet:
        logger.warning("Could not determine local subnet")
        return []

    logger.info(f"Scanning {subnet}.1-254 for Pixoo devices...")
    discovered = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=HTTP_SCAN_WORKERS) as executor:
        futures = {
            executor.submit(_check_pixoo_http, f"{subnet}.{i}"): i
            for i in range(1, 255)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                logger.info(f"Discovered Pixoo device at {result}")
                discovered.append(result)

    return discovered


def scan_network_udp() -> list[str]:
    """Scan the network for Pixoo devices using UDP broadcast.

    Returns:
        List of discovered IP addresses
    """
    discovered = []

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(DISCOVERY_TIMEOUT)

        sock.sendto(DISCOVERY_MESSAGE, ("<broadcast>", PIXOO_DISCOVERY_PORT))
        logger.debug(f"Sent discovery broadcast on port {PIXOO_DISCOVERY_PORT}")

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                ip_address = addr[0]
                logger.info(f"Discovered Pixoo device at {ip_address}")
                discovered.append(ip_address)
            except socket.timeout:
                break

    except OSError as e:
        logger.warning(f"UDP broadcast failed: {e}")
    finally:
        sock.close()

    return discovered


def scan_network() -> list[str]:
    """Scan the network for Pixoo devices.

    Tries UDP broadcast first (fast), then falls back to HTTP scan (reliable).

    Returns:
        List of discovered IP addresses
    """
    # Try fast UDP broadcast first
    logger.debug("Trying UDP broadcast discovery...")
    discovered = scan_network_udp()

    if discovered:
        return discovered

    # Fall back to HTTP scan
    logger.debug("UDP broadcast found nothing, trying HTTP scan...")
    return scan_network_http()


def discover_device(config_dir: Optional[Path] = None) -> Optional[str]:
    """Discover a Pixoo device.

    Priority:
    1. Check config file for manual IP
    2. Scan network for devices

    Args:
        config_dir: Path to config directory (default: ./config)

    Returns:
        IP address of discovered device, or None
    """
    config_dir = config_dir or Path("config")
    config_path = config_dir / "device.json"

    config = load_device_config(config_path)
    if config and config.ip_address:
        logger.info(f"Using configured IP address: {config.ip_address}")
        return config.ip_address

    logger.info("No configured IP, scanning network...")
    discovered = scan_network()

    if discovered:
        ip_address = discovered[0]
        logger.info(f"Using discovered device at {ip_address}")

        new_config = DeviceConfig(
            ip_address=ip_address,
            brightness=config.brightness if config else 100,
        )
        save_device_config(new_config, config_path)

        return ip_address

    logger.warning("No Pixoo devices found")
    return None


def get_device(config_dir: Optional[Path] = None) -> Optional[Pixoo]:
    """Get a connected Pixoo device.

    Args:
        config_dir: Path to config directory

    Returns:
        Connected Pixoo instance, or None if no device found
    """
    ip_address = discover_device(config_dir)
    if not ip_address:
        return None

    config_path = (config_dir or Path("config")) / "device.json"
    config = load_device_config(config_path)

    device = Pixoo(
        ip_address=ip_address,
        device_id=config.device_id if config and config.device_id else 0,
    )

    if device.ping():
        logger.info(f"Connected to Pixoo at {ip_address}")
        return device
    else:
        logger.error(f"Device at {ip_address} not responding")
        return None
