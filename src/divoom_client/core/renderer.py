"""Layout renderer for Pixoo displays."""

import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, Union

from PIL import Image

from divoom_client.core.fonts import get_font
from divoom_client.core.frame import Frame, parse_color
from divoom_client.models.layout import (
    ClockWidget,
    ConditionalColor,
    ImageWidget,
    Layout,
    LineWidget,
    RectWidget,
    TextWidget,
    Widget,
)

logger = logging.getLogger(__name__)


class ExpressionEvaluator:
    """Simple expression evaluator for conditional formatting."""

    def __init__(self, data: dict[str, Any]):
        """Initialize with data context.

        Args:
            data: Data dictionary for variable lookups (e.g., {"stocks": {"AAPL": {"price": 150}}})
        """
        self.data = data

    def get_value(self, path: str) -> Any:
        """Get a value from the data context using dot notation.

        Args:
            path: Dot-separated path (e.g., "stocks.AAPL.price")

        Returns:
            Value at path, or None if not found
        """
        parts = path.split(".")
        current = self.data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None
        return current

    def evaluate(self, expression: str) -> bool:
        """Evaluate a simple conditional expression.

        Supports: <, >, <=, >=, ==, !=
        Example: "stocks.AAPL.change < 0"

        Args:
            expression: Expression string

        Returns:
            Boolean result
        """
        # Pattern: variable operator value
        pattern = r"^\s*([\w.]+)\s*([<>=!]+)\s*(.+)\s*$"
        match = re.match(pattern, expression)
        if not match:
            logger.warning(f"Invalid expression: {expression}")
            return False

        var_path, operator, value_str = match.groups()
        var_value = self.get_value(var_path)

        if var_value is None:
            logger.debug(f"Variable not found: {var_path}")
            return False

        # Parse the comparison value
        try:
            if value_str.strip().lower() in ("true", "false"):
                compare_value = value_str.strip().lower() == "true"
            elif "." in value_str:
                compare_value = float(value_str)
            else:
                compare_value = int(value_str)
        except ValueError:
            compare_value = value_str.strip().strip("'\"")

        # Evaluate comparison
        try:
            if operator == "<":
                return var_value < compare_value
            elif operator == ">":
                return var_value > compare_value
            elif operator == "<=":
                return var_value <= compare_value
            elif operator == ">=":
                return var_value >= compare_value
            elif operator == "==":
                return var_value == compare_value
            elif operator == "!=":
                return var_value != compare_value
            else:
                logger.warning(f"Unknown operator: {operator}")
                return False
        except TypeError:
            logger.warning(f"Type mismatch in comparison: {var_value} {operator} {compare_value}")
            return False


class Renderer:
    """Renders layouts to frames."""

    def __init__(self, assets_dir: Optional[Path] = None):
        """Initialize renderer.

        Args:
            assets_dir: Directory containing image assets (icons, etc.)
        """
        self.assets_dir = assets_dir or Path("assets")
        self._image_cache: dict[str, Image.Image] = {}

    def resolve_color(
        self,
        color: Union[str, ConditionalColor],
        evaluator: ExpressionEvaluator,
    ) -> tuple[int, int, int]:
        """Resolve a color value, evaluating conditions if needed.

        Args:
            color: Static color string or ConditionalColor
            evaluator: Expression evaluator with data context

        Returns:
            RGB tuple
        """
        if isinstance(color, str):
            return parse_color(color)

        # Evaluate conditions in order
        for condition in color.conditions:
            if evaluator.evaluate(condition.when):
                return parse_color(condition.color)

        return parse_color(color.default)

    def format_value(self, format_str: str, value: Any) -> str:
        """Format a value using a format string.

        Args:
            format_str: Format string with {value} placeholder
            value: Value to format

        Returns:
            Formatted string
        """
        try:
            return format_str.format(value=value)
        except (ValueError, KeyError) as e:
            logger.warning(f"Format error: {e}")
            return str(value) if value is not None else ""

    def load_image(self, src: str, data: dict[str, Any]) -> Optional[Image.Image]:
        """Load an image, resolving data placeholders in path.

        Args:
            src: Image source path (may contain {data.path} placeholders)
            data: Data context for placeholder substitution

        Returns:
            PIL Image or None if load fails
        """
        # Substitute data placeholders in path
        def replace_placeholder(match: re.Match) -> str:
            path = match.group(1)
            parts = path.split(".")
            current = data
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part, "")
                else:
                    return ""
            return str(current)

        resolved_src = re.sub(r"\{([^}]+)\}", replace_placeholder, src)

        # Check cache
        if resolved_src in self._image_cache:
            return self._image_cache[resolved_src]

        # Try loading from assets directory
        image_path = self.assets_dir / resolved_src
        if not image_path.exists():
            # Try absolute path
            image_path = Path(resolved_src)

        if not image_path.exists():
            logger.warning(f"Image not found: {resolved_src}")
            return None

        try:
            img = Image.open(image_path)
            self._image_cache[resolved_src] = img
            return img
        except Exception as e:
            logger.error(f"Failed to load image {image_path}: {e}")
            return None

    def render_text_widget(
        self,
        widget: TextWidget,
        frame: Frame,
        data: dict[str, Any],
        evaluator: ExpressionEvaluator,
    ) -> None:
        """Render a text widget to the frame.

        Args:
            widget: Text widget configuration
            frame: Target frame
            data: Data context
            evaluator: Expression evaluator
        """
        # Get text content
        if widget.data_source:
            value = evaluator.get_value(widget.data_source)
            text = self.format_value(widget.format, value)
        else:
            text = widget.text or ""

        if not text:
            return

        # Get font and color
        font = get_font(widget.font)
        color = self.resolve_color(widget.color, evaluator)

        # Render each character
        x_offset = widget.x
        for char in text:
            pixels = font.render_char(char, color)
            for px, py, c in pixels:
                frame.set_pixel(x_offset + px, widget.y + py, c)
            x_offset += font.width + font.spacing

    def render_rect_widget(
        self,
        widget: RectWidget,
        frame: Frame,
        evaluator: ExpressionEvaluator,
    ) -> None:
        """Render a rectangle widget to the frame.

        Args:
            widget: Rectangle widget configuration
            frame: Target frame
            evaluator: Expression evaluator
        """
        color = self.resolve_color(widget.color, evaluator)
        frame.draw_rect(widget.x, widget.y, widget.width, widget.height, color, widget.filled)

    def render_line_widget(
        self,
        widget: LineWidget,
        frame: Frame,
        evaluator: ExpressionEvaluator,
    ) -> None:
        """Render a line widget to the frame.

        Args:
            widget: Line widget configuration
            frame: Target frame
            evaluator: Expression evaluator
        """
        color = self.resolve_color(widget.color, evaluator)
        frame.draw_line(widget.x1, widget.y1, widget.x2, widget.y2, color)

    def render_image_widget(
        self,
        widget: ImageWidget,
        frame: Frame,
        data: dict[str, Any],
    ) -> None:
        """Render an image widget to the frame.

        Args:
            widget: Image widget configuration
            frame: Target frame
            data: Data context for path substitution
        """
        img = self.load_image(widget.src, data)
        if img:
            frame.draw_image(widget.x, widget.y, img, widget.width, widget.height)

    def render_clock_widget(
        self,
        widget: ClockWidget,
        frame: Frame,
        evaluator: ExpressionEvaluator,
    ) -> None:
        """Render a clock widget to the frame.

        Args:
            widget: Clock widget configuration
            frame: Target frame
            evaluator: Expression evaluator
        """
        # Get current UTC time
        now_utc = datetime.now(timezone.utc)

        # Apply timezone offset
        offset_hours = widget.timezone_offset

        # Handle DST if auto_dst is enabled
        if widget.auto_dst:
            # Simple DST detection for US timezones
            # DST runs from second Sunday in March to first Sunday in November
            year = now_utc.year

            # Find second Sunday in March
            march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
            days_to_sunday = (6 - march_first.weekday()) % 7
            dst_start = march_first + timedelta(days=days_to_sunday + 7)  # Second Sunday
            dst_start = dst_start.replace(hour=2)  # 2 AM

            # Find first Sunday in November
            nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
            days_to_sunday = (6 - nov_first.weekday()) % 7
            dst_end = nov_first + timedelta(days=days_to_sunday)  # First Sunday
            dst_end = dst_end.replace(hour=2)  # 2 AM

            # Check if we're in DST period
            if dst_start <= now_utc < dst_end:
                offset_hours += 1  # Add 1 hour for DST

        # Apply offset
        local_time = now_utc + timedelta(hours=offset_hours)

        # Format time string
        if widget.format_24h:
            if widget.show_seconds:
                time_str = local_time.strftime("%H:%M:%S")
            else:
                time_str = local_time.strftime("%H:%M")
        else:
            if widget.show_seconds:
                time_str = local_time.strftime("%I:%M:%S")
            else:
                time_str = local_time.strftime("%I:%M")
            # Remove leading zero from hour for 12-hour format
            if time_str.startswith("0"):
                time_str = time_str[1:]
            # Add AM/PM
            time_str += local_time.strftime("%p").lower()[:1]  # 'a' or 'p'

        # Get font and color
        font = get_font(widget.font)
        color = self.resolve_color(widget.color, evaluator)

        # Render each character
        x_offset = widget.x
        for char in time_str:
            pixels = font.render_char(char, color)
            for px, py, c in pixels:
                frame.set_pixel(x_offset + px, widget.y + py, c)
            x_offset += font.width + font.spacing

    def render_widget(
        self,
        widget: Widget,
        frame: Frame,
        data: dict[str, Any],
        evaluator: ExpressionEvaluator,
    ) -> None:
        """Render a widget to the frame.

        Args:
            widget: Widget configuration
            frame: Target frame
            data: Data context
            evaluator: Expression evaluator
        """
        if isinstance(widget, TextWidget):
            self.render_text_widget(widget, frame, data, evaluator)
        elif isinstance(widget, RectWidget):
            self.render_rect_widget(widget, frame, evaluator)
        elif isinstance(widget, LineWidget):
            self.render_line_widget(widget, frame, evaluator)
        elif isinstance(widget, ImageWidget):
            self.render_image_widget(widget, frame, data)
        elif isinstance(widget, ClockWidget):
            self.render_clock_widget(widget, frame, evaluator)
        else:
            logger.warning(f"Unknown widget type: {type(widget)}")

    def render(self, layout: Layout, data: Optional[dict[str, Any]] = None) -> Frame:
        """Render a complete layout to a frame.

        Args:
            layout: Layout configuration
            data: Data context for dynamic content

        Returns:
            Rendered frame
        """
        data = data or {}
        evaluator = ExpressionEvaluator(data)

        # Create frame with background color
        frame = Frame(layout.background)

        # Render each widget
        for widget in layout.widgets:
            try:
                self.render_widget(widget, frame, data, evaluator)
            except Exception as e:
                logger.error(f"Error rendering widget {widget}: {e}")

        return frame
