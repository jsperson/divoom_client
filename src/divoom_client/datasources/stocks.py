"""Stock data fetcher using yfinance."""

import asyncio
import logging
from typing import Any

import yfinance as yf
from pydantic import Field

from divoom_client.datasources.base import DataSource, DataSourceConfig

logger = logging.getLogger(__name__)


class StockDataSourceConfig(DataSourceConfig):
    """Configuration for stock data source."""

    type: str = "stocks"
    symbols: list[str] = Field(default_factory=list, description="Stock symbols to track")
    refresh_seconds: int = Field(default=300, description="Refresh interval (default 5 min)")


class StockDataSource(DataSource):
    """Fetches stock data from Yahoo Finance."""

    def __init__(self, name: str, config: StockDataSourceConfig):
        """Initialize stock data source.

        Args:
            name: Data source name
            config: Stock configuration
        """
        super().__init__(name, config)
        self.symbols = config.symbols

    async def fetch(self) -> dict[str, Any]:
        """Fetch stock data for configured symbols.

        Returns:
            Dictionary with stock data keyed by symbol
        """
        if not self.symbols:
            return {}

        # yfinance is synchronous, run in executor
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._fetch_sync)
        return data

    def _fetch_sync(self) -> dict[str, Any]:
        """Synchronous fetch implementation."""
        result: dict[str, Any] = {}

        for symbol in self.symbols:
            try:
                ticker = yf.Ticker(symbol)

                # Get current price info
                info = ticker.fast_info

                current_price = getattr(info, 'last_price', None)
                previous_close = getattr(info, 'previous_close', None)

                if current_price is None:
                    # Fallback to history
                    hist = ticker.history(period="2d")
                    if not hist.empty:
                        current_price = float(hist['Close'].iloc[-1])
                        if len(hist) > 1:
                            previous_close = float(hist['Close'].iloc[-2])

                if current_price is not None:
                    change = 0.0
                    change_percent = 0.0

                    if previous_close and previous_close > 0:
                        change = current_price - previous_close
                        change_percent = (change / previous_close) * 100

                    result[symbol] = {
                        "price": round(current_price, 2),
                        "previous_close": round(previous_close, 2) if previous_close else None,
                        "change": round(change, 2),
                        "change_percent": round(change_percent, 2),
                        "symbol": symbol,
                    }
                    logger.debug(f"Fetched {symbol}: ${current_price:.2f} ({change:+.2f})")
                else:
                    logger.warning(f"Could not get price for {symbol}")
                    result[symbol] = {
                        "price": None,
                        "change": None,
                        "change_percent": None,
                        "symbol": symbol,
                        "error": "No price data available",
                    }

            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                result[symbol] = {
                    "price": None,
                    "change": None,
                    "change_percent": None,
                    "symbol": symbol,
                    "error": str(e),
                }

        return result


def create_stock_source(name: str, config_dict: dict[str, Any]) -> StockDataSource:
    """Factory function to create a stock data source.

    Args:
        name: Data source name
        config_dict: Configuration dictionary

    Returns:
        Configured StockDataSource
    """
    config = StockDataSourceConfig.model_validate(config_dict)
    return StockDataSource(name, config)
