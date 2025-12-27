"""Pixel buffer and frame management."""

from typing import Optional

from PIL import Image

PIXOO_SIZE = 64


def parse_color(color: str) -> tuple[int, int, int]:
    """Parse a hex color string to RGB tuple.

    Args:
        color: Hex color string (e.g., "#FF0000" or "FF0000")

    Returns:
        (R, G, B) tuple
    """
    color = color.lstrip("#")
    if len(color) != 6:
        raise ValueError(f"Invalid color: {color}")
    return (
        int(color[0:2], 16),
        int(color[2:4], 16),
        int(color[4:6], 16),
    )


class Frame:
    """A 64x64 pixel frame buffer for the Pixoo display."""

    def __init__(self, background: str = "#000000"):
        """Initialize frame with background color.

        Args:
            background: Background color as hex string
        """
        self.width = PIXOO_SIZE
        self.height = PIXOO_SIZE
        bg = parse_color(background)
        self._pixels: list[list[tuple[int, int, int]]] = [
            [bg for _ in range(self.width)] for _ in range(self.height)
        ]

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        """Set a single pixel.

        Args:
            x: X coordinate (0-63)
            y: Y coordinate (0-63)
            color: RGB tuple
        """
        if 0 <= x < self.width and 0 <= y < self.height:
            self._pixels[y][x] = color

    def get_pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """Get a single pixel.

        Args:
            x: X coordinate
            y: Y coordinate

        Returns:
            RGB tuple
        """
        if 0 <= x < self.width and 0 <= y < self.height:
            return self._pixels[y][x]
        return (0, 0, 0)

    def draw_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: tuple[int, int, int],
        filled: bool = True,
    ) -> None:
        """Draw a rectangle.

        Args:
            x: Top-left X coordinate
            y: Top-left Y coordinate
            width: Rectangle width
            height: Rectangle height
            color: RGB tuple
            filled: If True, fill the rectangle; otherwise draw outline only
        """
        if filled:
            for py in range(y, y + height):
                for px in range(x, x + width):
                    self.set_pixel(px, py, color)
        else:
            # Top and bottom edges
            for px in range(x, x + width):
                self.set_pixel(px, y, color)
                self.set_pixel(px, y + height - 1, color)
            # Left and right edges
            for py in range(y, y + height):
                self.set_pixel(x, py, color)
                self.set_pixel(x + width - 1, py, color)

    def draw_line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: tuple[int, int, int],
    ) -> None:
        """Draw a line using Bresenham's algorithm.

        Args:
            x1, y1: Start point
            x2, y2: End point
            color: RGB tuple
        """
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        x, y = x1, y1
        while True:
            self.set_pixel(x, y, color)
            if x == x2 and y == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

    def draw_image(
        self,
        x: int,
        y: int,
        image: Image.Image,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        """Draw an image onto the frame.

        Args:
            x: Top-left X coordinate
            y: Top-left Y coordinate
            image: PIL Image to draw
            width: Scale width (None = original)
            height: Scale height (None = original)
        """
        # Resize if needed
        if width or height:
            new_width = width or image.width
            new_height = height or image.height
            image = image.resize((new_width, new_height), Image.Resampling.NEAREST)

        # Convert to RGBA for transparency support
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        # Draw pixels
        for py in range(image.height):
            for px in range(image.width):
                r, g, b, a = image.getpixel((px, py))
                if a > 128:  # Simple alpha threshold
                    self.set_pixel(x + px, y + py, (r, g, b))

    def clear(self, color: str = "#000000") -> None:
        """Clear the frame with a solid color.

        Args:
            color: Fill color as hex string
        """
        c = parse_color(color)
        for y in range(self.height):
            for x in range(self.width):
                self._pixels[y][x] = c

    def to_pixels(self) -> list[tuple[int, int, int]]:
        """Convert frame to flat pixel list for Pixoo.

        Returns:
            List of 4096 RGB tuples in row-major order
        """
        pixels = []
        for row in self._pixels:
            pixels.extend(row)
        return pixels

    def to_image(self) -> Image.Image:
        """Convert frame to PIL Image.

        Returns:
            64x64 RGB PIL Image
        """
        img = Image.new("RGB", (self.width, self.height))
        for y in range(self.height):
            for x in range(self.width):
                img.putpixel((x, y), self._pixels[y][x])
        return img

    def save(self, path: str) -> None:
        """Save frame as image file.

        Args:
            path: Output file path (PNG, JPG, etc.)
        """
        self.to_image().save(path)
