"""Pixoo device communication."""

import base64
import logging
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

logger = logging.getLogger(__name__)

PIXOO_SIZE = 64


class Pixoo:
    """Client for communicating with Pixoo 64 devices."""

    def __init__(self, ip_address: str, device_id: int = 0, timeout: float = 5.0):
        """Initialize Pixoo client.

        Args:
            ip_address: IP address of the Pixoo device
            device_id: Device ID (default 0 for single device)
            timeout: Request timeout in seconds
        """
        self.ip_address = ip_address
        self.device_id = device_id
        self.timeout = timeout
        self._base_url = f"http://{ip_address}:80/post"

    def _send_command(self, command: dict) -> dict:
        """Send a command to the Pixoo device.

        Args:
            command: Command dictionary to send

        Returns:
            Response from device

        Raises:
            ConnectionError: If unable to connect to device
            ValueError: If device returns an error
        """
        try:
            response = requests.post(
                self._base_url,
                json=command,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("error_code", 0) != 0:
                raise ValueError(f"Device error: {data}")

            return data
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to Pixoo at {self.ip_address}: {e}") from e

    def get_device_info(self) -> dict:
        """Get device information.

        Returns:
            Device info dictionary containing DeviceName, DeviceId, etc.
        """
        return self._send_command({"Command": "Channel/GetAllConf"})

    def set_brightness(self, brightness: int) -> dict:
        """Set display brightness.

        Args:
            brightness: Brightness level (0-100)

        Returns:
            Response from device
        """
        brightness = max(0, min(100, brightness))
        return self._send_command({
            "Command": "Channel/SetBrightness",
            "Brightness": brightness,
        })

    def get_brightness(self) -> int:
        """Get current brightness level.

        Returns:
            Current brightness (0-100)
        """
        info = self.get_device_info()
        return info.get("Brightness", 100)

    def set_screen_on(self, on: bool = True) -> dict:
        """Turn screen on or off.

        Args:
            on: True to turn on, False to turn off

        Returns:
            Response from device
        """
        return self._send_command({
            "Command": "Channel/OnOffScreen",
            "OnOff": 1 if on else 0,
        })

    def set_channel(self, channel: int) -> dict:
        """Set the display channel.

        Args:
            channel: Channel index (0=Faces, 1=Cloud, 2=Visualizer, 3=Custom, 4=Black)

        Returns:
            Response from device
        """
        return self._send_command({
            "Command": "Channel/SetIndex",
            "SelectIndex": channel,
        })

    def reset_gif(self) -> dict:
        """Reset the HTTP GIF buffer. Call before sending new frames.

        Returns:
            Response from device
        """
        return self._send_command({"Command": "Draw/ResetHttpGifId"})

    def send_image(self, image: Image.Image, pic_num: int = 0) -> dict:
        """Send an image to the display.

        Args:
            image: PIL Image to display (will be resized to 64x64)
            pic_num: Picture number for animation frames (0 for static)

        Returns:
            Response from device
        """
        # Reset buffer before sending
        self.reset_gif()

        if image.size != (PIXOO_SIZE, PIXOO_SIZE):
            image = image.resize((PIXOO_SIZE, PIXOO_SIZE), Image.Resampling.NEAREST)

        if image.mode != "RGB":
            image = image.convert("RGB")

        pixels = list(image.getdata())
        pixel_data = []
        for r, g, b in pixels:
            pixel_data.extend([r, g, b])

        encoded = base64.b64encode(bytes(pixel_data)).decode("ascii")

        return self._send_command({
            "Command": "Draw/SendHttpGif",
            "PicNum": 1,
            "PicWidth": PIXOO_SIZE,
            "PicOffset": pic_num * PIXOO_SIZE * PIXOO_SIZE * 3,
            "PicID": pic_num,
            "PicSpeed": 1000,
            "PicData": encoded,
        })

    def send_pixels(self, pixels: list[tuple[int, int, int]]) -> dict:
        """Send raw pixel data to the display.

        Args:
            pixels: List of (R, G, B) tuples, 64x64 = 4096 pixels

        Returns:
            Response from device
        """
        if len(pixels) != PIXOO_SIZE * PIXOO_SIZE:
            raise ValueError(f"Expected {PIXOO_SIZE * PIXOO_SIZE} pixels, got {len(pixels)}")

        # Reset buffer before sending
        self.reset_gif()

        pixel_data = []
        for r, g, b in pixels:
            pixel_data.extend([r, g, b])

        encoded = base64.b64encode(bytes(pixel_data)).decode("ascii")

        return self._send_command({
            "Command": "Draw/SendHttpGif",
            "PicNum": 1,
            "PicWidth": PIXOO_SIZE,
            "PicOffset": 0,
            "PicID": 0,
            "PicSpeed": 1000,
            "PicData": encoded,
        })

    def clear(self, color: tuple[int, int, int] = (0, 0, 0)) -> dict:
        """Clear the display with a solid color.

        Args:
            color: RGB tuple for fill color (default black)

        Returns:
            Response from device
        """
        pixels = [color] * (PIXOO_SIZE * PIXOO_SIZE)
        return self.send_pixels(pixels)

    def ping(self) -> bool:
        """Check if device is reachable.

        Returns:
            True if device responds, False otherwise
        """
        try:
            self.get_device_info()
            return True
        except (ConnectionError, ValueError):
            return False

    def __repr__(self) -> str:
        return f"Pixoo(ip_address='{self.ip_address}', device_id={self.device_id})"
