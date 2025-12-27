"""Device discovery for Pixoo devices."""

import json
import logging
import socket
from pathlib import Path
from typing import Optional

from divoom_client.core.pixoo import Pixoo
from divoom_client.models.config import DeviceConfig

logger = logging.getLogger(__name__)

PIXOO_DISCOVERY_PORT = 8888
DISCOVERY_TIMEOUT = 3.0
DISCOVERY_MESSAGE = b"divoom"


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


def scan_network() -> list[str]:
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
        logger.warning(f"Network scan failed: {e}")
    finally:
        sock.close()

    return discovered


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
