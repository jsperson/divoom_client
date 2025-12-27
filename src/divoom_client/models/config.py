"""Configuration models."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeviceConfig(BaseModel):
    """Pixoo device configuration."""

    ip_address: Optional[str] = Field(default=None, description="Manual IP address of Pixoo device")
    brightness: int = Field(default=100, ge=0, le=100, description="Display brightness (0-100)")
    device_id: Optional[int] = Field(default=None, description="Device ID for multi-device setups")


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="DIVOOM_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    config_dir: Path = Field(
        default=Path("config"),
        description="Path to configuration directory",
    )
    openweather_api_key: Optional[str] = Field(
        default=None,
        description="OpenWeatherMap API key",
    )
    web_host: str = Field(default="0.0.0.0", description="Web UI host")
    web_port: int = Field(default=8080, description="Web UI port")
    log_level: str = Field(default="INFO", description="Logging level")
