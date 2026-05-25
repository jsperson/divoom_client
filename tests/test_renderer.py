"""Tests for layout rendering edge cases."""

from divoom_client.core.renderer import Renderer


def test_format_value_handles_missing_numeric_data() -> None:
    renderer = Renderer()

    assert renderer.format_value("${value:.2f}", None) == ""
