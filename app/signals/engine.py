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
        price = getattr(spy_info, 'last_price', None) or 0
        ma50 = getattr(spy_info, 'fifty_day_average', None) or 0
        vix_info = yf.Ticker("^VIX").fast_info
        vix = getattr(vix_info, 'last_price', None) or 0
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
    support = max(candidates_support) if candidates_support else None

    week52_high = float(high.max())  # full dataset range as 52w proxy
    candidates_resist = [v for v in [bb_upper, recent_high, week52_high] if v and v > price]
    resistance = min(candidates_resist) if candidates_resist else None

    return support, resistance, atr


def _build_entry_exit(price: float, support: Optional[float], resistance, atr):
    """Compute entry/stop/target. Returns None if R:R < 1.5."""
    if support is None:
        return None  # no technical support found, skip signal
    if atr is None or atr <= 0:
        atr = price * 0.02

    entry_low  = round(max(support * 1.002, price * 0.995), 2)
    entry_high = round(price * 1.005, 2)
    # Primary stop: 2×ATR below current price (standard chandelier stop)
    # Secondary: must be at or below support (technical floor)
    atr_stop  = round(price - 2 * atr, 2)
    tech_stop = round(support - 0.3 * atr, 2)   # small buffer below support
    stop = min(atr_stop, tech_stop)              # take the lower (wider) of the two
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

    # Individual stock trend: price vs 200-day SMA.
    # Requires 200+ bars; defaults to NEUTRAL for new/short-history stocks.
    ma200_series = ta.sma(close, length=200)
    ma200_val = float(ma200_series.iloc[-1]) if ma200_series is not None and not pd.isna(ma200_series.iloc[-1]) else None
    if ma200_val is None:
        stock_trend = "NEUTRAL"
    elif price > ma200_val:
        stock_trend = "UP"
    else:
        stock_trend = "DOWN"

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
            # Confidence based on histogram velocity (how fast it's moving away from zero),
            # not the absolute value on cross day (which is always near zero).
            velocity_pct = abs(curr_hist - prev_hist) / close.iloc[-1] * 100
            velocity_bonus = min(25, int(velocity_pct * 600))
            base_confidence = 55 + velocity_bonus  # range 55–80
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
            velocity_pct = abs(curr_hist - prev_hist) / close.iloc[-1] * 100
            velocity_bonus = min(25, int(velocity_pct * 600))
            base_confidence = 55 + velocity_bonus  # range 55–80
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
    # Event-based detection: fire only on crossing days, not every day in the zone.
    # Two events:
    #   entering oversold  (prev >= 30, curr < 30): potential setup, confidence 50–70
    #   exiting  oversold  (prev <  30, curr >= 30): reversal confirmed, confidence 60–90
    rsi = calc_rsi(close)
    rsi_valid = rsi.dropna()
    if len(rsi_valid) >= 2:
        prev_rsi = float(rsi_valid.iloc[-2])
        curr_rsi = float(rsi_valid.iloc[-1])
        if prev_rsi >= 30 and curr_rsi < 30:
            # Just entered oversold territory — potential BUY setup
            depth = 30 - curr_rsi
            base_confidence = min(75, int(depth / 30 * 25) + 50)   # 50–75
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="BUY",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI entered oversold ({curr_rsi:.1f} ↓ below 30)",
            ))
        elif prev_rsi < 30 and curr_rsi >= 30:
            # Exiting oversold — reversal confirmed, stronger signal
            depth = 30 - prev_rsi
            base_confidence = min(90, int(depth / 30 * 30) + 60)   # 60–90
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="BUY",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI exited oversold ({prev_rsi:.1f} → {curr_rsi:.1f}, reversal confirmed)",
            ))
        elif prev_rsi <= 70 and curr_rsi > 70:
            # Just entered overbought territory — potential SELL setup
            height = curr_rsi - 70
            base_confidence = min(75, int(height / 30 * 25) + 50)
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="SELL",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI entered overbought ({curr_rsi:.1f} ↑ above 70)",
            ))
        elif prev_rsi > 70 and curr_rsi <= 70:
            # Exiting overbought — reversal confirmed
            height = prev_rsi - 70
            base_confidence = min(90, int(height / 30 * 30) + 60)
            raw_signals.append(SignalResult(
                ticker=ticker,
                signal_type="SELL",
                indicator="RSI",
                price=price,
                target_price=None,
                confidence=base_confidence,
                signal_level="WEAK",
                message=f"RSI exited overbought ({prev_rsi:.1f} → {curr_rsi:.1f}, reversal confirmed)",
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
            message="EMA20/50 bullish cross: 20 EMA crossed above 50 EMA (BUY)",
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
            message="EMA20/50 bearish cross: 20 EMA crossed below 50 EMA (SELL)",
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

    # Bollinger interpretation is trend-context dependent:
    #   Upper band breach in UPTREND   → skip (riding the band = breakout, don't fade strength)
    #   Upper band breach in DOWN/NEUTRAL → SELL WATCH (mean reversion likely)
    #   Lower band breach in DOWNTREND → skip (falling knife, no bottom-picking)
    #   Lower band breach in UP/NEUTRAL  → BUY WATCH (oversold pullback in uptrend)
    if curr_upper is not None and price > float(curr_upper):
        if stock_trend != "UP":
            bollinger_signals.append(SignalResult(
                ticker=ticker,
                signal_type="SELL",
                indicator="BOLLINGER",
                price=price,
                target_price=float(curr_mid) if curr_mid is not None else None,
                confidence=50,
                signal_level="WATCH",
                message=f"Price above upper Bollinger band — mean reversion WATCH (trend: {stock_trend})",
            ))
    elif curr_lower is not None and price < float(curr_lower):
        if stock_trend != "DOWN":
            bollinger_signals.append(SignalResult(
                ticker=ticker,
                signal_type="BUY",
                indicator="BOLLINGER",
                price=price,
                target_price=float(curr_mid) if curr_mid is not None else None,
                confidence=50,
                signal_level="WATCH",
                message=f"Price below lower Bollinger band — oversold pullback WATCH (trend: {stock_trend})",
            ))

    # --- Confluence Detection ---
    buy_signals = [s for s in raw_signals if s.signal_type == "BUY"]
    sell_signals = [s for s in raw_signals if s.signal_type == "SELL"]

    final_signals: list[SignalResult] = []

    if len(buy_signals) >= 2:
        if regime != "BEAR" and stock_trend != "DOWN" and volume_ratio >= 1.2:
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

    if len(sell_signals) >= 2 and volume_ratio >= 1.2 and regime != "BULL" and stock_trend != "UP":
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
            entry_low=None,    # not applicable for SELL
            entry_high=None,   # not applicable for SELL
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
