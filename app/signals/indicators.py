"""Technical indicator calculations.

All functions are pure: no side effects, no DB, no network.
All functions accept a pd.Series of close prices and return a dict or pd.Series.
"""

import pandas as pd
import pandas_ta as ta


def calc_macd(close: pd.Series) -> dict:
    """Calculate MACD using standard 12/26/9 EMA parameters.

    Returns:
        dict with keys 'macd', 'signal', 'histogram' — each a pd.Series.
        Returns all-NaN series if calculation fails.
    """
    result = ta.macd(close)
    if result is None:
        nan_series = pd.Series([float("nan")] * len(close))
        return {"macd": nan_series, "signal": nan_series.copy(), "histogram": nan_series.copy()}
    # pandas_ta returns columns: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
    macd_col = [c for c in result.columns if c.startswith("MACD_")][0]
    hist_col = [c for c in result.columns if c.startswith("MACDh_")][0]
    signal_col = [c for c in result.columns if c.startswith("MACDs_")][0]
    return {
        "macd": result[macd_col].reset_index(drop=True),
        "signal": result[signal_col].reset_index(drop=True),
        "histogram": result[hist_col].reset_index(drop=True),
    }


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI.

    Returns:
        pd.Series of RSI values (0–100, NaN for initial periods).
    """
    result = ta.rsi(close, length=period)
    if result is None:
        return pd.Series([float("nan")] * len(close))
    return result.reset_index(drop=True)


def calc_ma_cross(close: pd.Series, fast: int = 20, slow: int = 50) -> dict:
    """Detect golden cross and death cross events.

    golden_cross: fast EMA crosses above slow EMA (fast > slow where previously fast <= slow)
    death_cross:  fast EMA crosses below slow EMA (fast < slow where previously fast >= slow)

    Returns:
        dict with keys 'golden_cross', 'death_cross' — each a bool pd.Series.
    """
    fast_ma = ta.ema(close, length=fast)
    slow_ma = ta.ema(close, length=slow)

    fast_above = (fast_ma > slow_ma).fillna(False)
    prev_fast_above = fast_above.shift(1).infer_objects(copy=False).fillna(False).astype(bool)

    golden_cross = (fast_above & ~prev_fast_above).astype(bool)
    death_cross = (~fast_above & prev_fast_above).astype(bool)

    return {
        "golden_cross": golden_cross.reset_index(drop=True),
        "death_cross": death_cross.reset_index(drop=True),
    }


def calc_bollinger(close: pd.Series, period: int = 20, std: float = 2.0) -> dict:
    """Calculate Bollinger Bands.

    Returns:
        dict with keys 'upper', 'mid', 'lower' — each a pd.Series.
    """
    result = ta.bbands(close, length=period, std=std)
    if result is None:
        nan_series = pd.Series([float("nan")] * len(close))
        return {"upper": nan_series, "mid": nan_series.copy(), "lower": nan_series.copy()}
    # pandas_ta returns columns: BBL_*, BBM_*, BBU_*, BBB_*, BBP_*
    lower_col = [c for c in result.columns if c.startswith("BBL_")][0]
    mid_col = [c for c in result.columns if c.startswith("BBM_")][0]
    upper_col = [c for c in result.columns if c.startswith("BBU_")][0]
    return {
        "upper": result[upper_col].reset_index(drop=True),
        "mid": result[mid_col].reset_index(drop=True),
        "lower": result[lower_col].reset_index(drop=True),
    }


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range for adaptive stop-loss."""
    result = ta.atr(high, low, close, length=period)
    if result is None:
        return pd.Series([float("nan")] * len(close), index=close.index)
    return result
