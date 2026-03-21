"""Tests for signal engine (run_signals)."""

import os
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.signals.engine import SignalResult, run_signals  # noqa: E402


def _make_flat_df(n: int = 100, price: float = 100.0) -> pd.DataFrame:
    """Create a flat OHLCV DataFrame (no strong signals expected)."""
    close = [price] * n
    return pd.DataFrame(
        {
            "Open": close,
            "High": [p + 0.5 for p in close],
            "Low": [p - 0.5 for p in close],
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


def _make_oversold_df() -> pd.DataFrame:
    """
    Create OHLCV where RSI crosses BELOW 30 on the last bar (entering oversold event),
    but NO MACD histogram cross.
    Pattern: stable then crash, keeping histogram monotonically negative.
    RSI is mocked in the test to control the crossing event precisely.
    """
    stable = [100.0] * 40
    crash = [100.0 - i * 1.5 for i in range(60)]  # steady drop, no bounce
    close = stable + crash
    n = len(close)
    return pd.DataFrame(
        {
            "Open": close,
            "High": [p + 0.5 for p in close],
            "Low": [p - 0.5 for p in close],
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


def _make_confluence_df() -> pd.DataFrame:
    """
    Create OHLCV that triggers MACD BUY + RSI exiting oversold on the last bar.
    Pattern: stable, hard crash (RSI < 30), then big recovery that flips
    MACD histogram positive AND brings RSI back above 30.
    RSI is mocked in the test to control the exit event precisely.
    """
    stable = [100.0] * 40
    crash = [100.0 - i * 1.5 for i in range(50)]  # drops to ~25
    recover = [crash[-1] + 5.0]  # bounce bar → MACD histogram crosses zero
    close = stable + crash + recover
    n = len(close)
    return pd.DataFrame(
        {
            "Open": close,
            "High": [p + 1.0 for p in close],
            "Low": [p - 1.0 for p in close],
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


def _make_price_above_upper_band_df() -> pd.DataFrame:
    """
    Create OHLCV where the last close is well above the Bollinger upper band.
    90 stable bars keep bands tight; then one massive spike breaks above upper band.
    """
    stable = [100.0] * 90
    spike = [200.0]  # single extreme spike above tight upper band
    close = stable + spike
    n = len(close)
    return pd.DataFrame(
        {
            "Open": close,
            "High": [p + 1.0 for p in close],
            "Low": [p - 1.0 for p in close],
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


class TestRunSignalsEmptyData:
    def test_returns_empty_list_when_fetch_returns_none(self):
        with patch("app.signals.engine.fetch_ohlcv", return_value=None):
            result = run_signals("FAKE")
        assert result == []


class TestRunSignalsNoSignal:
    def test_flat_data_returns_empty_or_only_watch(self):
        df = _make_flat_df()
        with patch("app.signals.engine.fetch_ohlcv", return_value=df):
            result = run_signals("FLAT")
        # Flat prices may produce no signals or only WATCH
        for sig in result:
            assert isinstance(sig, SignalResult)
            assert sig.signal_level == "WATCH"


class TestRunSignalsSingleIndicator:
    def test_rsi_oversold_returns_weak_buy(self):
        df = _make_oversold_df()
        n = len(df)
        # Mock RSI to produce a clear "entering oversold" event on the last bar:
        # prev_rsi = 31.5 (above 30), curr_rsi = 27.0 (below 30)
        rsi_mock = pd.Series([50.0] * (n - 2) + [31.5, 27.0])
        with patch("app.signals.engine.fetch_ohlcv", return_value=df), \
             patch("app.signals.engine.calc_rsi", return_value=rsi_mock):
            result = run_signals("OVERSOLD")

        # Should have at least one RSI BUY signal (entering oversold event)
        buy_signals = [s for s in result if s.signal_type == "BUY" and s.indicator == "RSI"]
        assert len(buy_signals) >= 1

        # If there's only one BUY indicator, it should be WEAK
        non_watch = [s for s in result if s.signal_level != "WATCH"]
        if len(non_watch) == 1:
            assert non_watch[0].signal_level == "WEAK"

    def test_signal_result_has_all_fields(self):
        df = _make_oversold_df()
        with patch("app.signals.engine.fetch_ohlcv", return_value=df):
            result = run_signals("OVERSOLD")

        for sig in result:
            assert hasattr(sig, "ticker")
            assert hasattr(sig, "signal_type")
            assert hasattr(sig, "indicator")
            assert hasattr(sig, "price")
            assert hasattr(sig, "target_price")
            assert hasattr(sig, "confidence")
            assert hasattr(sig, "signal_level")
            assert hasattr(sig, "message")
            assert sig.ticker == "OVERSOLD"
            assert sig.signal_type in ("BUY", "SELL", "WATCH")
            assert sig.signal_level in ("STRONG", "WEAK", "WATCH")
            assert 0 <= sig.confidence <= 95


class TestRunSignalsConfluence:
    def test_confluence_produces_strong_signal(self):
        df = _make_confluence_df()
        n = len(df)
        # Mock RSI to produce "exiting oversold" event on last bar (reversal confirmed):
        # prev_rsi = 24.0 (below 30), curr_rsi = 32.0 (above 30)
        rsi_mock = pd.Series([50.0] * (n - 2) + [24.0, 32.0])
        with patch("app.signals.engine.fetch_ohlcv", return_value=df), \
             patch("app.signals.engine.calc_rsi", return_value=rsi_mock), \
             patch("app.signals.engine._get_regime", return_value="BULL"), \
             patch("app.signals.engine._get_avg_volume", return_value=500_000):
            result = run_signals("CONF")

        strong_signals = [s for s in result if s.signal_level == "STRONG"]
        # We expect at least one strong signal from MACD + RSI confluence
        assert len(strong_signals) >= 1

    def test_strong_signal_has_plus_separator_in_indicator(self):
        df = _make_confluence_df()
        n = len(df)
        rsi_mock = pd.Series([50.0] * (n - 2) + [24.0, 32.0])
        with patch("app.signals.engine.fetch_ohlcv", return_value=df), \
             patch("app.signals.engine.calc_rsi", return_value=rsi_mock), \
             patch("app.signals.engine._get_regime", return_value="BULL"), \
             patch("app.signals.engine._get_avg_volume", return_value=500_000):
            result = run_signals("CONF")

        strong_signals = [s for s in result if s.signal_level == "STRONG"]
        if strong_signals:
            # confluence indicator should combine multiple indicator names
            assert "+" in strong_signals[0].indicator

    def test_strong_signal_confidence_boosted(self):
        df = _make_confluence_df()
        n = len(df)
        rsi_mock = pd.Series([50.0] * (n - 2) + [24.0, 32.0])
        with patch("app.signals.engine.fetch_ohlcv", return_value=df), \
             patch("app.signals.engine.calc_rsi", return_value=rsi_mock), \
             patch("app.signals.engine._get_regime", return_value="BULL"), \
             patch("app.signals.engine._get_avg_volume", return_value=500_000):
            result = run_signals("CONF")

        strong_signals = [s for s in result if s.signal_level == "STRONG"]
        if strong_signals:
            # Confluence adds bonus to max confidence, so should be reasonably high
            assert strong_signals[0].confidence >= 20


class TestBollingerWatchOnly:
    def test_price_above_upper_band_gives_watch_signal(self):
        df = _make_price_above_upper_band_df()
        with patch("app.signals.engine.fetch_ohlcv", return_value=df):
            result = run_signals("SPIKE")

        watch_signals = [s for s in result if s.signal_level == "WATCH"]
        # Should have at least one WATCH signal from Bollinger
        assert len(watch_signals) >= 1

    def test_bollinger_watch_signal_has_correct_indicator(self):
        df = _make_price_above_upper_band_df()
        with patch("app.signals.engine.fetch_ohlcv", return_value=df):
            result = run_signals("SPIKE")

        bollinger_signals = [s for s in result if s.indicator == "BOLLINGER"]
        assert len(bollinger_signals) >= 1
        for sig in bollinger_signals:
            assert sig.signal_level == "WATCH"
            assert sig.signal_type in ("BUY", "SELL", "WATCH")

    def test_bollinger_target_is_mid_band(self):
        df = _make_price_above_upper_band_df()
        with patch("app.signals.engine.fetch_ohlcv", return_value=df):
            result = run_signals("SPIKE")

        bollinger_signals = [s for s in result if s.indicator == "BOLLINGER"]
        if bollinger_signals:
            # target_price should be set (mid band)
            assert bollinger_signals[0].target_price is not None
            assert bollinger_signals[0].target_price > 0
