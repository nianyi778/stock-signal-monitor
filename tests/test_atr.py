import pandas as pd
import pytest
from app.signals.indicators import calc_atr

def test_atr_returns_series():
    high = pd.Series([105, 107, 106, 108, 110, 112, 111, 113, 115, 114,
                      116, 118, 117, 119, 121, 120, 122, 124, 123, 125])
    low  = pd.Series([100, 102, 101, 103, 105, 107, 106, 108, 110, 109,
                      111, 113, 112, 114, 116, 115, 117, 119, 118, 120])
    close= pd.Series([103, 105, 104, 106, 108, 110, 109, 111, 113, 112,
                      114, 116, 115, 117, 119, 118, 120, 122, 121, 123])
    result = calc_atr(high, low, close, period=14)
    assert isinstance(result, pd.Series)
    assert len(result) == len(close)
    valid = result.dropna()
    assert len(valid) > 0
    assert all(v > 0 for v in valid)

def test_atr_none_on_insufficient_data():
    high = pd.Series([105.0, 107.0])
    low  = pd.Series([100.0, 102.0])
    close= pd.Series([103.0, 105.0])
    result = calc_atr(high, low, close, period=14)
    assert result.dropna().empty
