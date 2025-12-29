"""Layout and widget models."""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class ColorCondition(BaseModel):
    """Conditional color based on data value."""

    when: str = Field(description="Condition expression, e.g., 'stocks.AAPL.change < 0'")
    color: str = Field(description="Hex color code, e.g., '#FF0000'")


class ConditionalColor(BaseModel):
    """Color with conditional overrides."""

    conditions: list[ColorCondition] = Field(default_factory=list)
    default: str = Field(default="#FFFFFF", description="Default color if no conditions match")


class TextWidget(BaseModel):
    """Text widget configuration."""

    type: Literal["text"] = "text"
    id: Optional[str] = None
    x: int = Field(ge=0, lt=64)
    y: int = Field(ge=0, lt=64)
    font: str = Field(default="5x7")
    data_source: Optional[str] = Field(default=None, description="Data source path, e.g., 'stocks.AAPL.price'")
    text: Optional[str] = Field(default=None, description="Static text (if no data_source)")
    format: str = Field(default="{value}", description="Format string for the value")
    color: Union[str, ConditionalColor] = Field(default="#FFFFFF")


class RectWidget(BaseModel):
    """Rectangle widget configuration."""

    type: Literal["rect"] = "rect"
    id: Optional[str] = None
    x: int = Field(ge=0, lt=64)
    y: int = Field(ge=0, lt=64)
    width: int = Field(gt=0, le=64)
    height: int = Field(gt=0, le=64)
    color: Union[str, ConditionalColor] = Field(default="#FFFFFF")
    filled: bool = Field(default=True)


class LineWidget(BaseModel):
    """Line widget configuration."""

    type: Literal["line"] = "line"
    id: Optional[str] = None
    x1: int = Field(ge=0, lt=64)
    y1: int = Field(ge=0, lt=64)
    x2: int = Field(ge=0, lt=64)
    y2: int = Field(ge=0, lt=64)
    color: Union[str, ConditionalColor] = Field(default="#FFFFFF")


class ImageWidget(BaseModel):
    """Image widget configuration."""

    type: Literal["image"] = "image"
    id: Optional[str] = None
    x: int = Field(ge=0, lt=64)
    y: int = Field(ge=0, lt=64)
    src: str = Field(description="Path to image file, supports {data.path} substitution")
    width: Optional[int] = Field(default=None, description="Scale width (None = original)")
    height: Optional[int] = Field(default=None, description="Scale height (None = original)")


class ClockWidget(BaseModel):
    """Clock widget configuration."""

    type: Literal["clock"] = "clock"
    id: Optional[str] = None
    x: int = Field(ge=0, lt=64)
    y: int = Field(ge=0, lt=64)
    font: str = Field(default="5x7")
    format_24h: bool = Field(default=False, description="Use 24-hour format")
    show_seconds: bool = Field(default=False, description="Show seconds")
    timezone_offset: float = Field(default=0, description="UTC offset in hours (e.g., -5 for EST)")
    auto_dst: bool = Field(default=True, description="Automatically adjust for daylight saving time")
    color: Union[str, ConditionalColor] = Field(default="#FFFFFF")


Widget = Union[TextWidget, RectWidget, LineWidget, ImageWidget, ClockWidget]


class Layout(BaseModel):
    """Display layout configuration."""

    name: str = Field(description="Layout name")
    background: str = Field(default="#000000", description="Background color")
    refresh_seconds: int = Field(default=60, ge=1, description="How often to refresh the display")
    widgets: list[Widget] = Field(default_factory=list)

    def get_widget(self, widget_id: str) -> Optional[Widget]:
        """Get a widget by ID."""
        for widget in self.widgets:
            if widget.id == widget_id:
                return widget
        return None
