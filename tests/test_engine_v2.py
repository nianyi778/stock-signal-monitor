"""Tests for upgraded signal engine with entry/stop/target/R:R logic."""
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest


def _make_df(n=60, vol_ratio_high=True):
    """Create synthetic OHLCV DataFrame."""
    close = pd.Series([100.0 + i * 0.5 for i in range(n)])
    high  = close + 2
    low   = close - 2
    volume = pd.Series([1_000_000 if vol_ratio_high else 300_000] * n)
    return pd.DataFrame({"Close": close, "High": high, "Low": low, "Volume": volume})


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_signal_has_new_fields(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    for s in signals:
        assert hasattr(s, "entry_low")
        assert hasattr(s, "entry_high")
        assert hasattr(s, "stop_price")
        assert hasattr(s, "warn_price")
        assert hasattr(s, "volume_ratio")
        assert hasattr(s, "regime")


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_strong_buy_has_rr_above_threshold(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    for s in signals:
        if s.signal_level == "STRONG" and s.rr_ratio is not None:
            assert s.rr_ratio >= 1.5, f"R:R {s.rr_ratio} below 1.5 threshold"


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_bear_regime_suppresses_strong_buy(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BEAR"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    strong_buys = [s for s in signals if s.signal_type == "BUY" and s.signal_level == "STRONG"]
    assert len(strong_buys) == 0, "No STRONG BUY signals allowed in BEAR regime"


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_low_volume_suppresses_strong_signals(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df(vol_ratio_high=False)
    mock_avgvol.return_value = 2_000_000  # ratio = 0.15x — below 1.2 threshold
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    strong = [s for s in signals if s.signal_level == "STRONG"]
    assert len(strong) == 0, "No STRONG signals on low volume"
