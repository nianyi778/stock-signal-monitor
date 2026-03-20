"""Signal engine: orchestrates indicator calculations and produces trading signals."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pandas_ta as ta

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
    # Only MACD, RSI, MA_CROSS contribute to confluence (not Bollinger)
    buy_signals = [s for s in raw_signals if s.signal_type == "BUY"]
    sell_signals = [s for s in raw_signals if s.signal_type == "SELL"]

    final_signals: list[SignalResult] = []

    if len(buy_signals) >= 2:
        indicator_str = "+".join(s.indicator for s in buy_signals)
        max_conf = max(s.confidence for s in buy_signals)
        confluence_boost = 10 * len(buy_signals)  # 2 indicators = +20, 3 = +30
        confluence_conf = min(95, max_conf + confluence_boost)
        strong_buy = SignalResult(
            ticker=ticker,
            signal_type="BUY",
            indicator=indicator_str,
            price=price,
            target_price=None,
            confidence=confluence_conf,
            signal_level="STRONG",
            message=f"Strong BUY: {indicator_str} confluence detected",
        )
        final_signals.append(strong_buy)

    if len(sell_signals) >= 2:
        indicator_str = "+".join(s.indicator for s in sell_signals)
        max_conf = max(s.confidence for s in sell_signals)
        confluence_boost = 10 * len(sell_signals)  # 2 indicators = +20, 3 = +30
        confluence_conf = min(95, max_conf + confluence_boost)
        strong_sell = SignalResult(
            ticker=ticker,
            signal_type="SELL",
            indicator=indicator_str,
            price=price,
            target_price=None,
            confidence=confluence_conf,
            signal_level="STRONG",
            message=f"Strong SELL: {indicator_str} confluence detected",
        )
        final_signals.append(strong_sell)

    # Add weak signals only if NO confluence exists for that direction
    has_strong_buy = any(s.signal_type == "BUY" and s.signal_level == "STRONG" for s in final_signals)
    has_strong_sell = any(s.signal_type == "SELL" and s.signal_level == "STRONG" for s in final_signals)

    if not has_strong_buy:
        final_signals.extend(buy_signals)  # already set to WEAK

    if not has_strong_sell:
        final_signals.extend(sell_signals)  # already set to WEAK

    # Always include Bollinger WATCH signals
    final_signals.extend(bollinger_signals)

    return final_signals
