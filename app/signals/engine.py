"""Signal engine: orchestrates indicator calculations and produces trading signals."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pandas_ta as ta
import yfinance as yf

from app.data.fetcher import fetch_ohlcv
from app.signals.indicators import calc_bollinger, calc_macd, calc_rsi


@dataclass
class SignalResult:
    ticker: str
    signal_type: str        # "BUY" / "SELL" / "WATCH"
    indicator: str          # "MACD", "RSI", "MA_CROSS", "BOLLINGER", or "MACD+RSI" etc.
    price: float
    target_price: Optional[float]   # estimated target (Bollinger mid, MA level, etc.)
    confidence: int         # 0-100
    signal_level: str       # "STRONG" / "WEAK" / "WATCH"
    message: str
    # V2 fields
    entry_low: float | None = None
    entry_high: float | None = None
    stop_price: float | None = None
    warn_price: float | None = None
    partial_tp: float | None = None
    rr_ratio: float | None = None
    volume_ratio: float | None = None
    regime: str | None = None
    atr: float | None = None


def _get_regime() -> str:
    """Check SPY vs 50-day MA and VIX. Returns BULL/BEAR/NEUTRAL."""
    try:
        spy_info = yf.Ticker("SPY").fast_info
        price = spy_info.get("lastPrice") or spy_info.get("last_price") or 0
        ma50 = spy_info.get("fiftyDayAverage") or spy_info.get("fifty_day_average") or 0
        vix_info = yf.Ticker("^VIX").fast_info
        vix = vix_info.get("lastPrice") or vix_info.get("last_price") or 0
        if price > ma50 and vix < 25:
            return "BULL"
        elif vix >= 25:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _get_avg_volume(df: pd.DataFrame) -> float:
    """Return 20-day average volume from OHLCV DataFrame."""
    vol = df.get("Volume")
    if vol is None or len(vol) < 5:
        return 0.0
    return float(vol.tail(20).mean())


def _calc_levels(df: pd.DataFrame, price: float):
    """Return (support, resistance, atr) from recent price data."""
    from app.signals.indicators import calc_atr, calc_bollinger
    close = df["Close"].reset_index(drop=True)
    high  = df["High"].reset_index(drop=True)
    low   = df["Low"].reset_index(drop=True)

    atr_series = calc_atr(high, low, close)
    atr = float(atr_series.dropna().iloc[-1]) if atr_series.dropna().shape[0] > 0 else None

    bb = calc_bollinger(close)
    bb_upper = float(bb["upper"].dropna().iloc[-1]) if bb["upper"].dropna().shape[0] > 0 else None
    bb_lower = float(bb["lower"].dropna().iloc[-1]) if bb["lower"].dropna().shape[0] > 0 else None

    recent_high = float(high.tail(20).max())
    recent_low  = float(low.tail(20).min())

    candidates_support = [v for v in [bb_lower, recent_low] if v and v < price]
    support = max(candidates_support) if candidates_support else price * 0.97

    candidates_resist = [v for v in [bb_upper, recent_high] if v and v > price]
    resistance = min(candidates_resist) if candidates_resist else None

    return support, resistance, atr


def _build_entry_exit(price: float, support: float, resistance, atr):
    """Compute entry/stop/target. Returns None if R:R < 1.5."""
    if atr is None or atr <= 0:
        atr = price * 0.02

    entry_low  = round(support * 1.002, 2)
    entry_high = round(price * 1.005, 2)
    stop       = round(support - 1.5 * atr, 2)
    warn       = round(stop + 0.75 * atr, 2)
    mid_entry  = (entry_low + entry_high) / 2

    if resistance is None or resistance <= mid_entry:
        return None

    rr = (resistance - mid_entry) / (mid_entry - stop)
    if rr < 1.5:
        return None

    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_price": stop,
        "warn_price": warn,
        "target_price": round(resistance, 2),
        "partial_tp": round(resistance * 0.95, 2),
        "rr_ratio": round(rr, 2),
    }


def run_signals(ticker: str) -> list[SignalResult]:
    """
    Run all indicator signals for a given ticker.

    Returns a list of SignalResult objects, applying confluence detection:
    - If 2+ BUY signals from MACD/RSI/MA_CROSS → STRONG BUY confluence
    - If 2+ SELL signals from MACD/RSI/MA_CROSS → STRONG SELL confluence
    - Otherwise individual signals are WEAK
    - Bollinger signals are always WATCH
    """
    df = fetch_ohlcv(ticker)
    if df is None:
        return []

    close = df["Close"].reset_index(drop=True)
    price = float(close.iloc[-1])

    regime = _get_regime()
    avg_vol = _get_avg_volume(df)
    last_vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0.0
    volume_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0.0

    raw_signals: list[SignalResult] = []

    # --- MACD ---
    macd_data = calc_macd(close)
    histogram = macd_data["histogram"]
    # Need at least 2 non-NaN values to detect a cross
    hist_valid = histogram.dropna()
    if len(hist_valid) >= 2:
        prev_hist = hist_valid.iloc[-2]
        curr_hist = hist_valid.iloc[-1]
        if prev_hist < 0 and curr_hist > 0:
            hist_pct = abs(curr_hist) / close.iloc[-1] * 100  # as percentage of price
            base_confidence = min(95, int(hist_pct * 500))  # 0.1% diff → 50 confidence
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="BUY",
                indicator="MACD",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"MACD histogram crossed above zero (BUY)",
            ))
        elif prev_hist > 0 and curr_hist < 0:
            hist_pct = abs(curr_hist) / close.iloc[-1] * 100  # as percentage of price
            base_confidence = min(95, int(hist_pct * 500))  # 0.1% diff → 50 confidence
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="SELL",
                indicator="MACD",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"MACD histogram crossed below zero (SELL)",
            ))

    # --- RSI ---
    rsi = calc_rsi(close)
    rsi_valid = rsi.dropna()
    if len(rsi_valid) >= 1:
        curr_rsi = float(rsi_valid.iloc[-1])
        if curr_rsi < 30:
            # Confidence based on distance below 30 threshold (RSI=0 → 95, RSI=29 → ~68)
            base_confidence = min(95, int((30 - curr_rsi) / 30 * 95) + 50)
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="BUY",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI oversold ({curr_rsi:.1f} < 30)",
            ))
        elif curr_rsi > 70:
            # Confidence based on distance above 70 threshold (RSI=100 → 95, RSI=71 → ~53)
            base_confidence = min(95, int((curr_rsi - 70) / 30 * 95) + 50)
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="SELL",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI overbought ({curr_rsi:.1f} > 70)",
            ))

    # --- MA Cross ---
    fast_ma = ta.ema(close, length=20)
    slow_ma = ta.ema(close, length=50)

    # Detect crosses inline (same logic as calc_ma_cross but reuses pre-computed MAs)
    fast_above = (fast_ma > slow_ma).fillna(False)
    prev_fast_above = fast_above.shift(1).infer_objects(copy=False).fillna(False).astype(bool)
    golden_cross = (fast_above & ~prev_fast_above).astype(bool)
    death_cross = (~fast_above & prev_fast_above).astype(bool)

    if golden_cross.iloc[-1]:
        slow_val = float(slow_ma.iloc[-1]) if not pd.isna(slow_ma.iloc[-1]) else None
        fast_val = float(fast_ma.iloc[-1])
        fast_pct_diff = (fast_val - float(slow_ma.iloc[-1])) / float(slow_ma.iloc[-1]) if slow_val else 0.0
        base_confidence = min(95, int(abs(fast_pct_diff) * 100 * 10))
        raw_signals.append(SignalResult(
            ticker=ticker,
            signal_type="BUY",
            indicator="MA_CROSS",
            price=price,
            target_price=slow_val,
            confidence=base_confidence,
            signal_level="WEAK",
            message="Golden cross: 20 EMA crossed above 50 EMA (BUY)",
        ))
    elif death_cross.iloc[-1]:
        slow_val = float(slow_ma.iloc[-1]) if not pd.isna(slow_ma.iloc[-1]) else None
        fast_val = float(fast_ma.iloc[-1])
        fast_pct_diff = (fast_val - float(slow_ma.iloc[-1])) / float(slow_ma.iloc[-1]) if slow_val else 0.0
        base_confidence = min(95, int(abs(fast_pct_diff) * 100 * 10))
        raw_signals.append(SignalResult(
            ticker=ticker,
            signal_type="SELL",
            indicator="MA_CROSS",
            price=price,
            target_price=slow_val,
            confidence=base_confidence,
            signal_level="WEAK",
            message="Death cross: 20 EMA crossed below 50 EMA (SELL)",
        ))

    # --- Bollinger ---
    boll = calc_bollinger(close)
    upper = boll["upper"]
    lower = boll["lower"]
    mid = boll["mid"]

    bollinger_signals: list[SignalResult] = []
    curr_upper = upper.dropna().iloc[-1] if upper.dropna().shape[0] > 0 else None
    curr_lower = lower.dropna().iloc[-1] if lower.dropna().shape[0] > 0 else None
    curr_mid = mid.dropna().iloc[-1] if mid.dropna().shape[0] > 0 else None

    if curr_upper is not None and price > float(curr_upper):
        bollinger_signals.append(SignalResult(
            ticker=ticker,
            signal_type="SELL",
            indicator="BOLLINGER",
            price=price,
            target_price=float(curr_mid) if curr_mid is not None else None,
            confidence=50,
            signal_level="WATCH",
            message=f"Price above upper Bollinger band (WATCH/SELL)",
        ))
    elif curr_lower is not None and price < float(curr_lower):
        bollinger_signals.append(SignalResult(
            ticker=ticker,
            signal_type="BUY",
            indicator="BOLLINGER",
            price=price,
            target_price=float(curr_mid) if curr_mid is not None else None,
            confidence=50,
            signal_level="WATCH",
            message=f"Price below lower Bollinger band (WATCH/BUY)",
        ))

    # --- Confluence Detection ---
    buy_signals = [s for s in raw_signals if s.signal_type == "BUY"]
    sell_signals = [s for s in raw_signals if s.signal_type == "SELL"]

    final_signals: list[SignalResult] = []

    if len(buy_signals) >= 2:
        if regime != "BEAR" and volume_ratio >= 1.2:
            support, resistance, atr = _calc_levels(df, price)
            ent = _build_entry_exit(price, support, resistance, atr)
            if ent:
                indicator_str = "+".join(s.indicator for s in buy_signals)
                max_conf = max(s.confidence for s in buy_signals)
                confluence_conf = min(95, max_conf + 10 * len(buy_signals))
                final_signals.append(SignalResult(
                    ticker=ticker,
                    signal_type="BUY",
                    indicator=indicator_str,
                    price=price,
                    target_price=ent["target_price"],
                    confidence=confluence_conf,
                    signal_level="STRONG",
                    message=f"Strong BUY: {indicator_str} confluence",
                    entry_low=ent["entry_low"],
                    entry_high=ent["entry_high"],
                    stop_price=ent["stop_price"],
                    warn_price=ent["warn_price"],
                    partial_tp=ent["partial_tp"],
                    rr_ratio=ent["rr_ratio"],
                    volume_ratio=volume_ratio,
                    regime=regime,
                    atr=atr,
                ))

    if len(sell_signals) >= 2 and volume_ratio >= 1.2:
        support, resistance, atr = _calc_levels(df, price)
        ent = _build_entry_exit(price, support, resistance, atr)
        indicator_str = "+".join(s.indicator for s in sell_signals)
        max_conf = max(s.confidence for s in sell_signals)
        confluence_conf = min(95, max_conf + 10 * len(sell_signals))
        final_signals.append(SignalResult(
            ticker=ticker,
            signal_type="SELL",
            indicator=indicator_str,
            price=price,
            target_price=ent["target_price"] if ent else None,
            confidence=confluence_conf,
            signal_level="STRONG",
            message=f"Strong SELL: {indicator_str} confluence",
            entry_low=ent["entry_low"] if ent else None,
            entry_high=ent["entry_high"] if ent else None,
            stop_price=ent["stop_price"] if ent else None,
            warn_price=ent["warn_price"] if ent else None,
            partial_tp=ent["partial_tp"] if ent else None,
            rr_ratio=ent["rr_ratio"] if ent else None,
            volume_ratio=volume_ratio,
            regime=regime,
            atr=atr,
        ))

    has_strong_buy = any(s.signal_type == "BUY" and s.signal_level == "STRONG" for s in final_signals)
    has_strong_sell = any(s.signal_type == "SELL" and s.signal_level == "STRONG" for s in final_signals)

    if not has_strong_buy:
        for s in buy_signals:
            s.volume_ratio = volume_ratio
            s.regime = regime
        final_signals.extend(buy_signals)

    if not has_strong_sell:
        for s in sell_signals:
            s.volume_ratio = volume_ratio
            s.regime = regime
        final_signals.extend(sell_signals)

    for s in bollinger_signals:
        s.volume_ratio = volume_ratio
        s.regime = regime
    final_signals.extend(bollinger_signals)

    return final_signals
