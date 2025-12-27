"""Base data source interface."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DataSourceConfig(BaseModel):
    """Base configuration for data sources."""

    type: str = Field(description="Data source type identifier")
    refresh_seconds: int = Field(default=300, ge=1, description="Refresh interval in seconds")
    enabled: bool = Field(default=True, description="Whether this source is enabled")


class DataSource(ABC):
    """Abstract base class for data sources."""

    def __init__(self, name: str, config: DataSourceConfig):
        """Initialize data source.

        Args:
            name: Unique name for this data source
            config: Configuration for this source
        """
        self.name = name
        self.config = config
        self._last_fetch: Optional[datetime] = None
        self._cached_data: Optional[dict[str, Any]] = None
        self._error: Optional[str] = None

    @property
    def source_type(self) -> str:
        """Return the type identifier for this data source."""
        return self.config.type

    @property
    def last_fetch(self) -> Optional[datetime]:
        """Return the timestamp of the last successful fetch."""
        return self._last_fetch

    @property
    def cached_data(self) -> Optional[dict[str, Any]]:
        """Return the cached data from the last fetch."""
        return self._cached_data

    @property
    def last_error(self) -> Optional[str]:
        """Return the last error message, if any."""
        return self._error

    @abstractmethod
    async def fetch(self) -> dict[str, Any]:
        """Fetch data from the source.

        Returns:
            Dictionary of fetched data

        Raises:
            Exception: If fetch fails
        """
        pass

    async def refresh(self) -> dict[str, Any]:
        """Refresh data from the source, updating cache.

        Returns:
            Dictionary of fetched data
        """
        try:
            data = await self.fetch()
            self._cached_data = data
            self._last_fetch = datetime.now()
            self._error = None
            logger.info(f"Data source '{self.name}' refreshed successfully")
            return data
        except Exception as e:
            self._error = str(e)
            logger.error(f"Data source '{self.name}' fetch failed: {e}")
            raise

    def get_data(self) -> dict[str, Any]:
        """Get current data (cached or empty).

        Returns:
            Cached data or empty dict
        """
        return self._cached_data or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', type='{self.source_type}')"
