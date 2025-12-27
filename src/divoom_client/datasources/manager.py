"""Data source manager for coordinating multiple data sources."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from divoom_client.datasources.base import DataSource
from divoom_client.datasources.generic import create_generic_source
from divoom_client.datasources.stocks import create_stock_source
from divoom_client.datasources.weather import create_weather_source

logger = logging.getLogger(__name__)

# Registry of data source factories
SOURCE_FACTORIES = {
    "stocks": create_stock_source,
    "weather": create_weather_source,
    "generic": create_generic_source,
}


class DataSourceManager:
    """Manages multiple data sources and provides unified data context."""

    def __init__(self):
        """Initialize the data source manager."""
        self._sources: dict[str, DataSource] = {}
        self._data_context: dict[str, Any] = {}

    @property
    def sources(self) -> dict[str, DataSource]:
        """Return all registered data sources."""
        return self._sources

    @property
    def data(self) -> dict[str, Any]:
        """Return the current unified data context."""
        return self._data_context

    def register(self, name: str, source: DataSource) -> None:
        """Register a data source.

        Args:
            name: Unique name for the source
            source: DataSource instance
        """
        self._sources[name] = source
        logger.info(f"Registered data source: {name} ({source.source_type})")

    def unregister(self, name: str) -> None:
        """Unregister a data source.

        Args:
            name: Name of source to remove
        """
        if name in self._sources:
            del self._sources[name]
            if name in self._data_context:
                del self._data_context[name]
            logger.info(f"Unregistered data source: {name}")

    def create_source(self, name: str, config: dict[str, Any]) -> DataSource:
        """Create and register a data source from configuration.

        Args:
            name: Name for the data source
            config: Configuration dictionary with 'type' key

        Returns:
            Created DataSource instance

        Raises:
            ValueError: If source type is unknown
        """
        source_type = config.get("type")
        if source_type not in SOURCE_FACTORIES:
            raise ValueError(
                f"Unknown data source type: {source_type}. "
                f"Available types: {list(SOURCE_FACTORIES.keys())}"
            )

        factory = SOURCE_FACTORIES[source_type]
        source = factory(name, config)
        self.register(name, source)
        return source

    def load_config(self, config_path: Path) -> None:
        """Load data sources from a configuration file.

        Args:
            config_path: Path to datasources.json file
        """
        if not config_path.exists():
            logger.warning(f"Data sources config not found: {config_path}")
            return

        try:
            with open(config_path) as f:
                config = json.load(f)

            sources = config.get("sources", {})
            for name, source_config in sources.items():
                if source_config.get("enabled", True):
                    self.create_source(name, source_config)

            logger.info(f"Loaded {len(self._sources)} data sources from {config_path}")

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load data sources config: {e}")
            raise

    async def refresh(self, name: str) -> dict[str, Any]:
        """Refresh a specific data source.

        Args:
            name: Name of the data source

        Returns:
            Updated data from the source

        Raises:
            KeyError: If source not found
        """
        if name not in self._sources:
            raise KeyError(f"Data source not found: {name}")

        source = self._sources[name]
        data = await source.refresh()
        self._data_context[name] = data
        return data

    async def refresh_all(self) -> dict[str, Any]:
        """Refresh all data sources concurrently.

        Returns:
            Complete data context
        """
        if not self._sources:
            return {}

        # Create refresh tasks for all sources
        tasks = []
        names = []
        for name, source in self._sources.items():
            if source.config.enabled:
                tasks.append(source.refresh())
                names.append(name)

        # Execute all refreshes concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Update data context
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to refresh {name}: {result}")
                # Keep old data if available
            else:
                self._data_context[name] = result

        return self._data_context

    def get_data_context(self) -> dict[str, Any]:
        """Get the current data context for rendering.

        Returns:
            Dictionary with all data source data
        """
        # Build context from cached data
        context = {}
        for name, source in self._sources.items():
            data = source.get_data()
            if data:
                context[name] = data
        return context

    def get_source(self, name: str) -> Optional[DataSource]:
        """Get a data source by name.

        Args:
            name: Source name

        Returns:
            DataSource or None
        """
        return self._sources.get(name)

    def clear(self) -> None:
        """Clear all data sources."""
        self._sources.clear()
        self._data_context.clear()

    def __repr__(self) -> str:
        return f"DataSourceManager(sources={list(self._sources.keys())})"
