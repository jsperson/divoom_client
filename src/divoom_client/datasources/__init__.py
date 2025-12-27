"""Data sources for divoom_client."""

from divoom_client.datasources.base import DataSource, DataSourceConfig
from divoom_client.datasources.stocks import StockDataSource, StockDataSourceConfig
from divoom_client.datasources.weather import WeatherDataSource, WeatherDataSourceConfig
from divoom_client.datasources.generic import GenericDataSource, GenericDataSourceConfig
from divoom_client.datasources.manager import DataSourceManager

__all__ = [
    "DataSource",
    "DataSourceConfig",
    "StockDataSource",
    "StockDataSourceConfig",
    "WeatherDataSource",
    "WeatherDataSourceConfig",
    "GenericDataSource",
    "GenericDataSourceConfig",
    "DataSourceManager",
]
