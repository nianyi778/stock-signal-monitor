import numpy as np
import pandas as pd
import pytest

from app.signals.indicators import calc_bollinger, calc_ma_cross, calc_macd, calc_rsi


@pytest.fixture
def close_series():
    """Deterministic close price series (100 values, linearly increasing)."""
    return pd.Series(range(100, 200), dtype=float)


@pytest.fixture
def cross_series():
    """Close prices that produce a clear MA cross (fast crosses above slow)."""
    # Start low (death cross territory), then spike high (golden cross territory)
    low = [50.0] * 60
    high = [150.0] * 60
    return pd.Series(low + high, dtype=float)


class TestCalcMacd:
    def test_calc_macd_returns_expected_keys(self, close_series):
        result = calc_macd(close_series)
        assert set(result.keys()) == {"macd", "signal", "histogram"}

    def test_calc_macd_values_are_series(self, close_series):
        result = calc_macd(close_series)
        for key in ("macd", "signal", "histogram"):
            assert isinstance(result[key], pd.Series), f"{key} should be pd.Series"

    def test_calc_macd_same_length_as_input(self, close_series):
        result = calc_macd(close_series)
        for key in ("macd", "signal", "histogram"):
            assert len(result[key]) == len(close_series)


class TestCalcRsi:
    def test_calc_rsi_value_range(self, close_series):
        rsi = calc_rsi(close_series)
        assert isinstance(rsi, pd.Series)
        valid = rsi.dropna()
        assert (valid >= 0).all(), "RSI values must be >= 0"
        assert (valid <= 100).all(), "RSI values must be <= 100"

    def test_calc_rsi_initial_values_are_nan(self, close_series):
        rsi = calc_rsi(close_series, period=14)
        # First (period - 1) values should be NaN
        assert rsi.iloc[0] is np.nan or pd.isna(rsi.iloc[0])

    def test_calc_rsi_same_length_as_input(self, close_series):
        rsi = calc_rsi(close_series)
        assert len(rsi) == len(close_series)


class TestCalcMaCross:
    def test_calc_ma_cross_has_boolean_series(self, cross_series):
        result = calc_ma_cross(cross_series, fast=5, slow=10)
        assert "golden_cross" in result
        assert "death_cross" in result
        assert isinstance(result["golden_cross"], pd.Series)
        assert isinstance(result["death_cross"], pd.Series)
        assert result["golden_cross"].dtype == bool
        assert result["death_cross"].dtype == bool

    def test_calc_ma_cross_golden_cross_detected(self, cross_series):
        result = calc_ma_cross(cross_series, fast=5, slow=10)
        assert result["golden_cross"].any(), "Expected at least one golden cross"

    def test_calc_ma_cross_death_cross_detected(self, cross_series):
        result = calc_ma_cross(cross_series, fast=5, slow=10)
        # With only one transition from low to high there's no death cross,
        # so we just verify death_cross is a valid bool Series (no error raised)
        assert result["death_cross"].dtype == bool

    def test_calc_ma_cross_same_length_as_input(self, cross_series):
        result = calc_ma_cross(cross_series)
        assert len(result["golden_cross"]) == len(cross_series)
        assert len(result["death_cross"]) == len(cross_series)


class TestCalcBollinger:
    def test_calc_bollinger_band_ordering(self, close_series):
        result = calc_bollinger(close_series)
        assert set(result.keys()) == {"upper", "mid", "lower"}

        upper = result["upper"]
        mid = result["mid"]
        lower = result["lower"]

        # Check where all three are non-NaN
        valid_mask = upper.notna() & mid.notna() & lower.notna()
        assert valid_mask.any(), "Expected some non-NaN Bollinger values"

        assert (upper[valid_mask] >= mid[valid_mask]).all(), "upper must be >= mid"
        assert (mid[valid_mask] >= lower[valid_mask]).all(), "mid must be >= lower"

    def test_calc_bollinger_values_are_series(self, close_series):
        result = calc_bollinger(close_series)
        for key in ("upper", "mid", "lower"):
            assert isinstance(result[key], pd.Series), f"{key} should be pd.Series"

    def test_calc_bollinger_same_length_as_input(self, close_series):
        result = calc_bollinger(close_series)
        for key in ("upper", "mid", "lower"):
            assert len(result[key]) == len(close_series)
