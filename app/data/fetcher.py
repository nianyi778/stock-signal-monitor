"""Data fetcher module for fetching stock data from yfinance."""

from typing import Optional

import pandas as pd
import yfinance


def fetch_ohlcv(
    ticker: str, period: str = "3mo", interval: str = "1d"
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV (Open, High, Low, Close, Volume) data for a given ticker.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        period: Time period for data fetch (default: "3mo")
        interval: Data interval (default: "1d" for daily)

    Returns:
        DataFrame with OHLCV columns if data is valid, None otherwise.
        Returns None if the result is empty or has fewer than 30 rows.
    """
    try:
        df = yfinance.download(ticker, period=period, interval=interval, progress=False)

        # Check if data is empty or has insufficient rows
        if df.empty or len(df) < 30:
            return None

        # yfinance >= 0.2.31 returns MultiIndex columns for single ticker
        # e.g. ('Close', 'AAPL') — flatten to just 'Close'
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        return df
    except Exception:
        return None


def get_current_price(ticker: str) -> Optional[float]:
    """
    Fetch the current price for a given ticker.

    Tries fast_info first, then falls back to info.
    Returns None on any exception or if price data is unavailable.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Current price as float, or None if unavailable.
    """
    try:
        ticker_obj = yfinance.Ticker(ticker)

        # Try fast_info first
        try:
            price = ticker_obj.fast_info.get("lastPrice")
            if price is not None:
                return float(price)
        except (AttributeError, KeyError, TypeError):
            pass

        # Fall back to info
        try:
            price = ticker_obj.info.get("currentPrice")
            if price is not None:
                return float(price)
        except (AttributeError, KeyError, TypeError):
            pass

        # If neither worked, return None
        return None
    except Exception:
        return None
