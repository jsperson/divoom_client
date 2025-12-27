"""Generic REST API data fetcher."""

import asyncio
import logging
import os
import re
from typing import Any, Optional

import requests
from jsonpath_ng import parse as jsonpath_parse
from pydantic import Field

from divoom_client.datasources.base import DataSource, DataSourceConfig

logger = logging.getLogger(__name__)


class GenericDataSourceConfig(DataSourceConfig):
    """Configuration for generic REST API data source."""

    type: str = "generic"
    url: str = Field(description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers")
    params: dict[str, str] = Field(default_factory=dict, description="Query parameters")
    body: Optional[dict[str, Any]] = Field(default=None, description="Request body (for POST)")
    json_path: Optional[str] = Field(
        default=None,
        description="JSONPath expression to extract data (e.g., '$.data.value')"
    )
    json_paths: dict[str, str] = Field(
        default_factory=dict,
        description="Multiple JSONPath extractions (e.g., {'temp': '$.main.temp'})"
    )
    timeout: int = Field(default=30, description="Request timeout in seconds")
    refresh_seconds: int = Field(default=300, description="Refresh interval")


class GenericDataSource(DataSource):
    """Fetches data from any REST API."""

    def __init__(self, name: str, config: GenericDataSourceConfig):
        """Initialize generic data source.

        Args:
            name: Data source name
            config: Generic API configuration
        """
        super().__init__(name, config)
        self.url = config.url
        self.method = config.method.upper()
        self.headers = self._resolve_env_vars(config.headers)
        self.params = self._resolve_env_vars(config.params)
        self.body = config.body
        self.json_path = config.json_path
        self.json_paths = config.json_paths
        self.timeout = config.timeout

    def _resolve_env_vars(self, data: dict[str, str]) -> dict[str, str]:
        """Resolve environment variable references in dictionary values.

        Args:
            data: Dictionary with potential ${ENV_VAR} references

        Returns:
            Dictionary with resolved values
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                result[key] = os.environ.get(env_var, "")
            else:
                result[key] = value
        return result

    def _extract_jsonpath(self, data: Any, path: str) -> Any:
        """Extract value using JSONPath expression.

        Args:
            data: JSON data to extract from
            path: JSONPath expression

        Returns:
            Extracted value or None
        """
        try:
            expr = jsonpath_parse(path)
            matches = expr.find(data)
            if matches:
                if len(matches) == 1:
                    return matches[0].value
                return [m.value for m in matches]
            return None
        except Exception as e:
            logger.warning(f"JSONPath extraction failed for '{path}': {e}")
            return None

    async def fetch(self) -> dict[str, Any]:
        """Fetch data from the configured API.

        Returns:
            Dictionary with fetched/extracted data
        """
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._fetch_sync)
        return data

    def _fetch_sync(self) -> dict[str, Any]:
        """Synchronous fetch implementation."""
        try:
            kwargs: dict[str, Any] = {
                "headers": self.headers,
                "params": self.params,
                "timeout": self.timeout,
            }

            if self.method in ("POST", "PUT", "PATCH") and self.body:
                kwargs["json"] = self.body

            response = requests.request(self.method, self.url, **kwargs)
            response.raise_for_status()

            # Parse JSON response
            try:
                data = response.json()
            except ValueError:
                # Non-JSON response
                return {"raw": response.text}

            # Extract using JSONPath if configured
            if self.json_path:
                extracted = self._extract_jsonpath(data, self.json_path)
                return {"value": extracted, "raw": data}

            if self.json_paths:
                result: dict[str, Any] = {}
                for key, path in self.json_paths.items():
                    result[key] = self._extract_jsonpath(data, path)
                result["raw"] = data
                return result

            # Return full response if no extraction configured
            return data

        except requests.exceptions.RequestException as e:
            logger.error(f"Generic API fetch failed: {e}")
            raise


def create_generic_source(name: str, config_dict: dict[str, Any]) -> GenericDataSource:
    """Factory function to create a generic data source.

    Args:
        name: Data source name
        config_dict: Configuration dictionary

    Returns:
        Configured GenericDataSource
    """
    config = GenericDataSourceConfig.model_validate(config_dict)
    return GenericDataSource(name, config)
