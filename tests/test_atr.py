import pandas as pd
import pytest
from app.signals.indicators import calc_atr


class TestCalcAtr:
    @pytest.fixture
    def ohlc_series(self):
        high = pd.Series([105, 107, 106, 108, 110, 112, 111, 113, 115, 114,
                          116, 118, 117, 119, 121, 120, 122, 124, 123, 125], dtype=float)
        low  = pd.Series([100, 102, 101, 103, 105, 107, 106, 108, 110, 109,
                          111, 113, 112, 114, 116, 115, 117, 119, 118, 120], dtype=float)
        close = pd.Series([103, 105, 104, 106, 108, 110, 109, 111, 113, 112,
                           114, 116, 115, 117, 119, 118, 120, 122, 121, 123], dtype=float)
        return high, low, close

    def test_atr_returns_series(self, ohlc_series):
        high, low, close = ohlc_series
        result = calc_atr(high, low, close, period=14)
        assert isinstance(result, pd.Series)

    def test_atr_same_length_as_input(self, ohlc_series):
        high, low, close = ohlc_series
        result = calc_atr(high, low, close, period=14)
        assert len(result) == len(close)

    def test_atr_positive_values(self, ohlc_series):
        high, low, close = ohlc_series
        result = calc_atr(high, low, close, period=14)
        valid = result.dropna()
        assert len(valid) > 0
        assert all(v > 0 for v in valid)

    def test_atr_insufficient_data(self):
        high = pd.Series([105.0, 107.0])
        low  = pd.Series([100.0, 102.0])
        close = pd.Series([103.0, 105.0])
        result = calc_atr(high, low, close, period=14)
        assert result.dropna().empty
