from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from app.data.fetcher import fetch_ohlcv, get_current_price


class TestFetchOHLCV:
    """Test suite for fetch_ohlcv function."""

    def test_fetch_ohlcv_success(self):
        """Test successful OHLCV fetch with valid DataFrame."""
        # Create a mock DataFrame with OHLCV columns
        mock_data = {
            "Open": [100.0, 101.0, 102.0] + [103.0] * 30,
            "High": [101.0, 102.0, 103.0] + [104.0] * 30,
            "Low": [99.0, 100.0, 101.0] + [102.0] * 30,
            "Close": [100.5, 101.5, 102.5] + [103.5] * 30,
            "Volume": [1000000, 1100000, 1200000] + [1300000] * 30,
        }
        dates = [datetime.now() - timedelta(days=i) for i in range(33)][::-1]
        mock_df = pd.DataFrame(mock_data, index=dates)

        with patch("app.data.fetcher.yfinance.download") as mock_download:
            mock_download.return_value = mock_df
            result = fetch_ohlcv("AAPL")

            assert result is not None
            assert isinstance(result, pd.DataFrame)
            assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
            assert len(result) == 33
            mock_download.assert_called_once_with(
                "AAPL", period="14mo", interval="1d", progress=False
            )

    def test_fetch_ohlcv_with_custom_period_and_interval(self):
        """Test OHLCV fetch with custom period and interval."""
        mock_data = {
            "Open": [100.0] * 50,
            "High": [101.0] * 50,
            "Low": [99.0] * 50,
            "Close": [100.5] * 50,
            "Volume": [1000000] * 50,
        }
        dates = [datetime.now() - timedelta(hours=i) for i in range(50)][::-1]
        mock_df = pd.DataFrame(mock_data, index=dates)

        with patch("app.data.fetcher.yfinance.download") as mock_download:
            mock_download.return_value = mock_df
            result = fetch_ohlcv("AAPL", period="1mo", interval="1h")

            assert result is not None
            assert len(result) == 50
            mock_download.assert_called_once_with(
                "AAPL", period="1mo", interval="1h", progress=False
            )

    def test_fetch_ohlcv_empty(self):
        """Test OHLCV fetch returns None for empty DataFrame."""
        mock_df = pd.DataFrame()

        with patch("app.data.fetcher.yfinance.download") as mock_download:
            mock_download.return_value = mock_df
            result = fetch_ohlcv("INVALID")

            assert result is None
            mock_download.assert_called_once()

    def test_fetch_ohlcv_insufficient_data(self):
        """Test OHLCV fetch returns None for insufficient data (< 30 rows)."""
        # Only 20 rows of data
        mock_data = {
            "Open": [100.0] * 20,
            "High": [101.0] * 20,
            "Low": [99.0] * 20,
            "Close": [100.5] * 20,
            "Volume": [1000000] * 20,
        }
        dates = [datetime.now() - timedelta(days=i) for i in range(20)][::-1]
        mock_df = pd.DataFrame(mock_data, index=dates)

        with patch("app.data.fetcher.yfinance.download") as mock_download:
            mock_download.return_value = mock_df
            result = fetch_ohlcv("AAPL")

            assert result is None
            mock_download.assert_called_once()

    def test_fetch_ohlcv_exactly_30_rows(self):
        """Test OHLCV fetch succeeds with exactly 30 rows."""
        mock_data = {
            "Open": [100.0] * 30,
            "High": [101.0] * 30,
            "Low": [99.0] * 30,
            "Close": [100.5] * 30,
            "Volume": [1000000] * 30,
        }
        dates = [datetime.now() - timedelta(days=i) for i in range(30)][::-1]
        mock_df = pd.DataFrame(mock_data, index=dates)

        with patch("app.data.fetcher.yfinance.download") as mock_download:
            mock_download.return_value = mock_df
            result = fetch_ohlcv("AAPL")

            assert result is not None
            assert len(result) == 30


class TestGetCurrentPrice:
    """Test suite for get_current_price function."""

    def test_get_current_price_success_fast_info(self):
        """Test successful price fetch from fast_info."""
        mock_ticker = MagicMock()
        mock_fast_info = MagicMock()
        mock_fast_info.last_price = 175.50
        mock_ticker.fast_info = mock_fast_info

        with patch("app.data.fetcher.yfinance.Ticker") as mock_ticker_class:
            mock_ticker_class.return_value = mock_ticker
            result = get_current_price("AAPL")

            assert result == 175.50
            mock_ticker_class.assert_called_once_with("AAPL")

    def test_get_current_price_fallback_to_info(self):
        """Test price fetch falls back to info when fast_info fails."""
        mock_ticker = MagicMock()
        # Simulate fast_info being unavailable or missing
        mock_ticker.fast_info = {}
        mock_ticker.info = {"currentPrice": 175.50}

        with patch("app.data.fetcher.yfinance.Ticker") as mock_ticker_class:
            mock_ticker_class.return_value = mock_ticker
            result = get_current_price("AAPL")

            assert result == 175.50

    def test_get_current_price_exception_handling(self):
        """Test graceful return of None on exception."""
        with patch("app.data.fetcher.yfinance.Ticker") as mock_ticker_class:
            mock_ticker_class.side_effect = Exception("Network error")
            result = get_current_price("INVALID")

            assert result is None

    def test_get_current_price_missing_field(self):
        """Test graceful return of None when price field is missing."""
        mock_ticker = MagicMock()
        mock_ticker.fast_info = {}
        mock_ticker.info = {}

        with patch("app.data.fetcher.yfinance.Ticker") as mock_ticker_class:
            mock_ticker_class.return_value = mock_ticker
            result = get_current_price("AAPL")

            assert result is None
