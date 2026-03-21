"""Signal outcome tracker — evaluates STRONG signals after 5 NYSE trading days."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Optional

import yfinance as yf
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# NYSE trading calendar (lazy-loaded to avoid import at module level)
_NYSE_CALENDAR = None


def _get_nyse_calendar():
    global _NYSE_CALENDAR
    if _NYSE_CALENDAR is None:
        import pandas_market_calendars as mcal
        _NYSE_CALENDAR = mcal.get_calendar("NYSE")
    return _NYSE_CALENDAR


def _get_target_et_date(triggered_at_utc: datetime, trading_days: int = 5) -> date:
    """
    Return the date that is exactly `trading_days` NYSE trading days after
    the ET date of `triggered_at_utc`.

    triggered_at_utc must be UTC-aware (or naive UTC).
    """
    # Convert UTC → ET
    et_tz = timezone(timedelta(hours=-5))   # ET standard; close enough for daily resolution
    triggered_et = triggered_at_utc.astimezone(et_tz)
    start_date = triggered_et.date()

    cal = _get_nyse_calendar()
    # Get a wide window — 14 calendar days is always enough for 5 trading days
    end_window = start_date + timedelta(days=14)
    schedule = cal.schedule(
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_window.strftime("%Y-%m-%d"),
    )
    trading_dates = [d.date() for d in schedule.index]

    # Exclude the signal day itself; we want N days *after*
    future_days = [d for d in trading_dates if d > start_date]
    if len(future_days) < trading_days:
        # Fallback: just add calendar days (shouldn't happen with 14-day window)
        return start_date + timedelta(days=trading_days + 2)
    return future_days[trading_days - 1]


def _classify_result(
    min_low: float,
    day5_close: float,
    entry_price: float,
    stop_price: Optional[float],
) -> str:
    """
    Path-dependent WIN / LOSS / NEUTRAL classification.

    LOSS  — stop_price is set AND min_low (over 5 days) ≤ stop_price
    WIN   — stop not breached AND day-5 close > entry × 1.01
    NEUTRAL — everything else
    """
    if stop_price is not None and min_low <= stop_price:
        return "LOSS"
    if day5_close > entry_price * 1.01:
        return "WIN"
    return "NEUTRAL"


def evaluate_signal_outcomes(db: Session) -> int:
    """
    Find pushed STRONG signals whose 5-NYSE-trading-day evaluation date has
    passed and no SignalOutcome exists yet. Fetch OHLCV, classify, persist.

    Returns the count of newly written SignalOutcome rows.
    """
    from app.models import Signal, SignalOutcome

    today_et = datetime.now(timezone(timedelta(hours=-5))).date()
    written = 0

    # All pushed STRONG signals that might need evaluation
    candidates = (
        db.query(Signal)
        .filter(Signal.pushed == True, Signal.signal_level == "STRONG")  # noqa: E712
        .all()
    )

    for sig in candidates:
        # Skip if already evaluated
        existing = db.query(SignalOutcome).filter_by(signal_id=sig.id).first()
        if existing:
            continue

        # Compute target evaluation date
        triggered = sig.triggered_at
        if triggered.tzinfo is None:
            triggered = triggered.replace(tzinfo=UTC)

        target_date = _get_target_et_date(triggered)

        # Guard: evaluation date must be strictly in the past (market fully closed)
        if target_date >= today_et:
            continue

        # Fetch 5-day OHLCV window (start = day after signal date, end = day after target)
        et_tz = timezone(timedelta(hours=-5))
        signal_et_date = triggered.astimezone(et_tz).date()
        start_str = (signal_et_date + timedelta(days=1)).strftime("%Y-%m-%d")
        end_str   = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            df = yf.download(sig.ticker, start=start_str, end=end_str,
                             progress=False, auto_adjust=True)
            if df is None or df.empty or len(df) < 1:
                logger.warning(f"No OHLCV data for {sig.ticker} [{start_str}:{end_str}]")
                continue

            # Flatten MultiIndex if present (yfinance >= 0.2.31)
            import pandas as pd
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

            min_low    = float(df["Low"].min())
            day5_close = float(df["Close"].iloc[-1])

        except Exception as e:
            logger.warning(f"OHLCV fetch failed for {sig.ticker}: {e}")
            continue

        entry_price = sig.price
        outcome_pct = round((day5_close - entry_price) / entry_price * 100, 4)
        result = _classify_result(min_low, day5_close, entry_price, sig.stop_price)

        outcome = SignalOutcome(
            signal_id     = sig.id,
            ticker        = sig.ticker,
            indicator     = sig.indicator,
            signal_type   = sig.signal_type,
            entry_price   = entry_price,
            stop_price    = sig.stop_price,
            outcome_price = day5_close,
            outcome_pct   = outcome_pct,
            result        = result,
            evaluated_at  = datetime.now(UTC),
        )
        db.add(outcome)
        db.commit()
        written += 1
        logger.info(f"Evaluated {sig.ticker} signal #{sig.id}: {result} ({outcome_pct:+.2f}%)")

    return written
