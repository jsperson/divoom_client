"""Weather data fetcher using OpenWeatherMap."""

import asyncio
import logging
import os
from typing import Any, Optional

import requests
from pydantic import Field

from divoom_client.datasources.base import DataSource, DataSourceConfig

logger = logging.getLogger(__name__)

OPENWEATHER_API_URL = "https://api.openweathermap.org/data/2.5/weather"


class WeatherDataSourceConfig(DataSourceConfig):
    """Configuration for weather data source."""

    type: str = "weather"
    api_key: Optional[str] = Field(default=None, description="OpenWeatherMap API key")
    location: str = Field(default="New York,US", description="Location (city,country)")
    units: str = Field(default="imperial", description="Units: imperial, metric, or standard")
    refresh_seconds: int = Field(default=600, description="Refresh interval (default 10 min)")


class WeatherDataSource(DataSource):
    """Fetches weather data from OpenWeatherMap."""

    def __init__(self, name: str, config: WeatherDataSourceConfig):
        """Initialize weather data source.

        Args:
            name: Data source name
            config: Weather configuration
        """
        super().__init__(name, config)
        self.location = config.location
        self.units = config.units
        # Support env var substitution for API key
        self.api_key = self._resolve_api_key(config.api_key)

    def _resolve_api_key(self, api_key: Optional[str]) -> Optional[str]:
        """Resolve API key, supporting environment variable substitution.

        Args:
            api_key: API key or ${ENV_VAR} reference

        Returns:
            Resolved API key
        """
        if not api_key:
            # Try environment variable
            return os.environ.get("OPENWEATHER_API_KEY") or os.environ.get("DIVOOM_OPENWEATHER_API_KEY")

        if api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            return os.environ.get(env_var)

        return api_key

    async def fetch(self) -> dict[str, Any]:
        """Fetch weather data for configured location.

        Returns:
            Dictionary with weather data
        """
        if not self.api_key:
            raise ValueError("OpenWeatherMap API key not configured")

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._fetch_sync)
        return data

    def _fetch_sync(self) -> dict[str, Any]:
        """Synchronous fetch implementation."""
        params = {
            "q": self.location,
            "appid": self.api_key,
            "units": self.units,
        }

        try:
            response = requests.get(OPENWEATHER_API_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Extract relevant fields
            main = data.get("main", {})
            weather = data.get("weather", [{}])[0]
            wind = data.get("wind", {})

            result = {
                "temp": round(main.get("temp", 0)),
                "feels_like": round(main.get("feels_like", 0)),
                "temp_min": round(main.get("temp_min", 0)),
                "temp_max": round(main.get("temp_max", 0)),
                "humidity": main.get("humidity", 0),
                "pressure": main.get("pressure", 0),
                "description": weather.get("description", ""),
                "icon": weather.get("icon", ""),
                "main": weather.get("main", ""),
                "wind_speed": round(wind.get("speed", 0)),
                "wind_deg": wind.get("deg", 0),
                "location": data.get("name", self.location),
                "country": data.get("sys", {}).get("country", ""),
            }

            logger.debug(
                f"Fetched weather for {result['location']}: "
                f"{result['temp']}° {result['description']}"
            )
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Weather fetch failed: {e}")
            raise


# Weather icon mapping for display
WEATHER_ICONS = {
    "01d": "clear_day",
    "01n": "clear_night",
    "02d": "partly_cloudy_day",
    "02n": "partly_cloudy_night",
    "03d": "cloudy",
    "03n": "cloudy",
    "04d": "overcast",
    "04n": "overcast",
    "09d": "rain",
    "09n": "rain",
    "10d": "rain_day",
    "10n": "rain_night",
    "11d": "thunderstorm",
    "11n": "thunderstorm",
    "13d": "snow",
    "13n": "snow",
    "50d": "mist",
    "50n": "mist",
}


def get_weather_icon_name(icon_code: str) -> str:
    """Map OpenWeatherMap icon code to icon name.

    Args:
        icon_code: OWM icon code (e.g., "01d")

    Returns:
        Icon name for display
    """
    return WEATHER_ICONS.get(icon_code, "unknown")


def create_weather_source(name: str, config_dict: dict[str, Any]) -> WeatherDataSource:
    """Factory function to create a weather data source.

    Args:
        name: Data source name
        config_dict: Configuration dictionary

    Returns:
        Configured WeatherDataSource
    """
    config = WeatherDataSourceConfig.model_validate(config_dict)
    return WeatherDataSource(name, config)
