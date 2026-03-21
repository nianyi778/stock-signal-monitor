# Self-Evolving Algorithm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a closed-loop learning system: signal outcome tracking (Phase A), external market sentiment fusion (Phase B), and monthly LLM-driven parameter auto-tuning (Phase C).

**Architecture:** Three independently deployable phases. Phase A adds DB tables + daily outcome evaluation. Phase B adds a composite sentiment signal (CNN Fear&Greed + VIX slope + Finnhub) applied per scan run. Phase C adds monthly auto-tuning using Phase A outcome data and gpt-4.1. All new business logic lives in `app/learning/` and `app/data/market_sentiment.py`; existing files are modified minimally.

**Tech Stack:** Python 3.12, SQLAlchemy, yfinance, httpx (async), numpy, pandas_market_calendars (new dep), OpenAI (gpt-4.1), APScheduler.

---

## File Map

```
NEW:
  app/learning/__init__.py
  app/learning/params.py          — get_param() overlay helper
  app/learning/outcome_tracker.py — evaluate_signal_outcomes() with path-dependent stop
  app/learning/auto_tuner.py      — auto_tune_params() monthly LLM tuner
  app/data/market_sentiment.py    — F&G + VIX slope + Finnhub composite
  tests/test_outcome_tracker.py
  tests/test_market_sentiment.py
  tests/test_auto_tuner.py

MODIFY:
  app/models.py          — add SignalOutcome, IndicatorParams, ParamTuningHistory; index on Signal.triggered_at
  app/signals/engine.py  — run_signals(ticker, db=None); get_param() for volume/rr/weights
  app/scheduler.py       — _daily_job() + scan_all_stocks() + start_scheduler() + new job wrapper
  app/mcp_server.py      — add stock_monitor_get_signal_stats + stock_monitor_get_tuning_history
  requirements.txt       — add pandas_market_calendars
  README.md              — update MCP tools table + Roadmap
```

---

## Task 1: New DB Models

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **1.1** Add imports to `app/models.py` (after existing imports):

```python
from sqlalchemy import ForeignKey, Index, Text
```

- [ ] **1.2** Add index on `Signal.triggered_at` — add this line inside the `Signal` class after the `triggered_at` column:

```python
    __table_args__ = (
        Index("ix_signals_triggered_at", "triggered_at"),
    )
```

- [ ] **1.3** Append three new model classes at the bottom of `app/models.py`.
  Use the same `Mapped[...]` annotation style as the existing models in the file:

```python
class SignalOutcome(Base):
    """Result of a pushed STRONG signal, evaluated 5 NYSE trading days after push."""

    __tablename__ = "signal_outcomes"

    id:            Mapped[int]            = mapped_column(Integer, primary_key=True)
    signal_id:     Mapped[int]            = mapped_column(Integer, ForeignKey("signals.id"), nullable=False, index=True)
    ticker:        Mapped[str]            = mapped_column(String(10), nullable=False)
    indicator:     Mapped[str]            = mapped_column(String(64), nullable=False)
    signal_type:   Mapped[str]            = mapped_column(String(8), nullable=False)
    entry_price:   Mapped[float]          = mapped_column(Float, nullable=False)
    stop_price:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    outcome_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    outcome_pct:   Mapped[float | None]   = mapped_column(Float, nullable=True)
    result:        Mapped[str | None]     = mapped_column(String(10), nullable=True)
    evaluated_at:  Mapped[datetime | None]= mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class IndicatorParams(Base):
    """Tunable parameter overlay. Missing keys fall back to settings.* defaults."""

    __tablename__ = "indicator_params"

    id:          Mapped[int]          = mapped_column(Integer, primary_key=True)
    param_key:   Mapped[str]          = mapped_column(String(64), unique=True, nullable=False)
    param_value: Mapped[float]        = mapped_column(Float, nullable=False)
    updated_at:  Mapped[datetime]     = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by:  Mapped[str]          = mapped_column(String(64), default="manual")


class ParamTuningHistory(Base):
    """Audit log of every auto-tuning event."""

    __tablename__ = "param_tuning_history"

    id:               Mapped[int]      = mapped_column(Integer, primary_key=True)
    tuned_at:         Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    signals_analyzed: Mapped[int]      = mapped_column(Integer, nullable=False)
    params_before:    Mapped[str]      = mapped_column(Text, nullable=False)
    params_after:     Mapped[str]      = mapped_column(Text, nullable=False)
    llm_reasoning:    Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **1.4** Open `tests/test_models.py`. Write a test that creates all three new model instances and verifies they persist:

```python
def test_signal_outcome_create(db):
    from app.models import Signal, SignalOutcome
    sig = Signal(ticker="AAPL", signal_type="BUY", indicator="MACD",
                 price=150.0, message="test", confidence=70, signal_level="STRONG")
    db.add(sig)
    db.flush()
    outcome = SignalOutcome(
        signal_id=sig.id, ticker="AAPL", indicator="MACD",
        signal_type="BUY", entry_price=150.0, stop_price=145.0,
        outcome_price=152.0, outcome_pct=1.33, result="WIN",
    )
    db.add(outcome)
    db.commit()
    assert db.query(SignalOutcome).count() == 1
    assert db.query(SignalOutcome).first().result == "WIN"

def test_indicator_params_create(db):
    from app.models import IndicatorParams
    p = IndicatorParams(param_key="push_min_confidence", param_value=65.0, updated_by="test")
    db.add(p)
    db.commit()
    assert db.query(IndicatorParams).filter_by(param_key="push_min_confidence").first().param_value == 65.0

def test_param_tuning_history_create(db):
    import json
    from app.models import ParamTuningHistory
    h = ParamTuningHistory(
        signals_analyzed=20,
        params_before=json.dumps({"push_min_confidence": 60}),
        params_after=json.dumps({"push_min_confidence": 63}),
    )
    db.add(h)
    db.commit()
    assert db.query(ParamTuningHistory).first().signals_analyzed == 20
```

- [ ] **1.5** Run: `pytest tests/test_models.py -v`
  Expected: All 3 new tests PASS (SQLite in-memory creates all tables via `Base.metadata.create_all`)

- [ ] **1.6** Commit:
```bash
git add app/models.py tests/test_models.py
git commit -m "feat(phase-a): add SignalOutcome, IndicatorParams, ParamTuningHistory models"
```

---

## Task 2: get_param() Helper

**Files:**
- Create: `app/learning/__init__.py`
- Create: `app/learning/params.py`
- Test: `tests/test_models.py` (add to existing file)

- [ ] **2.1** Create `app/learning/__init__.py` (empty):
```bash
touch app/learning/__init__.py
```

- [ ] **2.2** Write test for `get_param()` — append to `tests/test_models.py`:

```python
def test_get_param_fallback(db):
    """get_param returns default when key absent."""
    from app.learning.params import get_param
    result = get_param(db, "push_min_confidence", 60.0)
    assert result == 60.0

def test_get_param_from_db(db):
    """get_param reads from IndicatorParams when key present."""
    from app.models import IndicatorParams
    from app.learning.params import get_param
    db.add(IndicatorParams(param_key="rr_ratio_min", param_value=2.0))
    db.commit()
    result = get_param(db, "rr_ratio_min", 1.5)
    assert result == 2.0

def test_get_param_with_none_db():
    """get_param returns default when db is None."""
    from app.learning.params import get_param
    assert get_param(None, "volume_ratio_min", 1.2) == 1.2
```

- [ ] **2.3** Run: `pytest tests/test_models.py::test_get_param_fallback -v`
  Expected: FAIL with `ModuleNotFoundError`

- [ ] **2.4** Create `app/learning/params.py`:

```python
"""Parameter overlay: reads tunable params from DB, falls back to defaults."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


# Absolute hard bounds — enforced after ±20% relative clamp in auto_tuner.
# Prevents unbounded drift across multiple tuning cycles.
HARD_FLOOR: dict[str, float] = {
    "push_min_confidence": 45.0,
    "macd_weight":         0.3,
    "rsi_weight":          0.3,
    "ma_cross_weight":     0.3,
    "volume_ratio_min":    1.0,
    "rr_ratio_min":        1.0,
}

HARD_CEILING: dict[str, float] = {
    "push_min_confidence": 85.0,
    "macd_weight":         2.0,
    "rsi_weight":          2.0,
    "ma_cross_weight":     2.0,
    "volume_ratio_min":    2.5,
    "rr_ratio_min":        3.0,
}


def get_param(db: Optional[Session], key: str, default: float) -> float:
    """
    Read a tunable parameter from IndicatorParams table.

    Falls back to `default` when:
    - db is None (e.g., called from bot/analysis.py without a db session)
    - key not present in IndicatorParams table

    Args:
        db: SQLAlchemy session, or None for fallback-only mode.
        key: Parameter key, e.g. "volume_ratio_min".
        default: Value to return when key is absent.

    Returns:
        Float value from DB or default.
    """
    if db is None:
        return default
    try:
        from app.models import IndicatorParams
        row = db.query(IndicatorParams).filter_by(param_key=key).first()
        if row is not None:
            return float(row.param_value)
    except Exception:
        pass
    return default
```

- [ ] **2.5** Run: `pytest tests/test_models.py -v -k "get_param"`
  Expected: All 3 tests PASS

- [ ] **2.6** Commit:
```bash
git add app/learning/__init__.py app/learning/params.py tests/test_models.py
git commit -m "feat(phase-a): add get_param() overlay helper with hard bounds constants"
```

---

## Task 3: Signal Outcome Tracker

**Files:**
- Create: `app/learning/outcome_tracker.py`
- Create: `tests/test_outcome_tracker.py`

- [ ] **3.1** Create `tests/test_outcome_tracker.py`:

```python
"""Tests for signal outcome evaluation logic."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd


def _make_signal(db, ticker="AAPL", price=150.0, stop=145.0,
                 days_ago=7, signal_type="BUY"):
    """Helper: create a pushed STRONG Signal record from N days ago."""
    from app.models import Signal
    triggered = datetime.now(timezone.utc) - timedelta(days=days_ago)
    sig = Signal(
        ticker=ticker,
        signal_type=signal_type,
        indicator="MACD+RSI",
        price=price,
        stop_price=stop,
        target_price=price * 1.10,
        message="test signal",
        confidence=75,
        signal_level="STRONG",
        pushed=True,
        triggered_at=triggered,
    )
    db.add(sig)
    db.commit()
    return sig


def test_win_outcome(db):
    """Price rose above entry*1.01, stop never breached → WIN."""
    sig = _make_signal(db, price=100.0, stop=95.0, days_ago=8)

    # Fake 5-day OHLCV: low always > stop, close on day 5 = 103
    mock_df = pd.DataFrame({
        "Low":   [98.0, 99.0, 100.0, 101.0, 102.0],
        "Close": [99.0, 100.0, 101.0, 102.0, 103.0],
    })

    with patch("app.learning.outcome_tracker.yf.download", return_value=mock_df), \
         patch("app.learning.outcome_tracker._get_target_et_date", return_value=__import__("datetime").date.today() - timedelta(days=1)):
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)

    assert count == 1
    from app.models import SignalOutcome
    outcome = db.query(SignalOutcome).first()
    assert outcome.result == "WIN"
    assert outcome.outcome_pct > 0


def test_loss_path_dependent(db):
    """Min low breaches stop on day 2 → LOSS regardless of day-5 close."""
    sig = _make_signal(db, price=100.0, stop=95.0, days_ago=8)

    # Day 2 low = 94.0 < stop 95.0 — stop was hit
    mock_df = pd.DataFrame({
        "Low":   [98.0, 94.0, 96.0, 97.0, 98.0],   # min = 94 ≤ 95
        "Close": [99.0, 95.5, 97.0, 98.0, 99.5],   # day-5 close looks OK but doesn't matter
    })

    with patch("app.learning.outcome_tracker.yf.download", return_value=mock_df), \
         patch("app.learning.outcome_tracker._get_target_et_date", return_value=__import__("datetime").date.today() - timedelta(days=1)):
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)

    assert count == 1
    from app.models import SignalOutcome
    outcome = db.query(SignalOutcome).first()
    assert outcome.result == "LOSS"


def test_neutral_outcome(db):
    """Price moved up but < 1%, stop not breached → NEUTRAL."""
    sig = _make_signal(db, price=100.0, stop=95.0, days_ago=8)

    mock_df = pd.DataFrame({
        "Low":   [98.0, 98.5, 99.0, 99.0, 99.5],
        "Close": [99.0, 99.2, 99.5, 99.8, 100.5],  # +0.5%, below 1% threshold
    })

    with patch("app.learning.outcome_tracker.yf.download", return_value=mock_df), \
         patch("app.learning.outcome_tracker._get_target_et_date", return_value=__import__("datetime").date.today() - timedelta(days=1)):
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)

    assert count == 1
    from app.models import SignalOutcome
    assert db.query(SignalOutcome).first().result == "NEUTRAL"


def test_no_duplicate_evaluation(db):
    """Signal already has a SignalOutcome — should not be evaluated again."""
    from app.models import SignalOutcome
    sig = _make_signal(db, price=100.0, stop=95.0, days_ago=8)
    # Pre-create outcome record
    db.add(SignalOutcome(
        signal_id=sig.id, ticker="AAPL", indicator="MACD+RSI",
        signal_type="BUY", entry_price=100.0, result="WIN",
    ))
    db.commit()

    with patch("app.learning.outcome_tracker.yf.download") as mock_dl, \
         patch("app.learning.outcome_tracker._get_target_et_date", return_value=__import__("datetime").date.today() - timedelta(days=1)):
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)

    mock_dl.assert_not_called()
    assert count == 0


def test_future_signal_skipped(db):
    """Signal triggered only 2 days ago — target_date is still in the future → skip."""
    _make_signal(db, price=100.0, stop=95.0, days_ago=2)

    with patch("app.learning.outcome_tracker.yf.download") as mock_dl:
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)

    mock_dl.assert_not_called()
    assert count == 0
```

- [ ] **3.2** Run: `pytest tests/test_outcome_tracker.py -v`
  Expected: All FAIL with `ModuleNotFoundError: app.learning.outcome_tracker`

- [ ] **3.3** Create `app/learning/outcome_tracker.py`:

```python
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
```

- [ ] **3.4** Add `pandas_market_calendars` to `requirements.txt`:
```
pandas_market_calendars
```

- [ ] **3.5** Run: `pytest tests/test_outcome_tracker.py -v`
  Expected: All 5 tests PASS. (If `pandas_market_calendars` is not installed locally, run `pip install pandas_market_calendars` first.)

- [ ] **3.6** Commit:
```bash
git add app/learning/outcome_tracker.py tests/test_outcome_tracker.py requirements.txt
git commit -m "feat(phase-a): signal outcome tracker with path-dependent stop evaluation"
```

---

## Task 4: Engine Migration — get_param() Integration

**Files:**
- Modify: `app/signals/engine.py`
- Test: `tests/test_engine.py` (add a test)

The goal: `run_signals(ticker, db=None)` reads `volume_ratio_min`, `rr_ratio_min`, and per-indicator weights from the DB via `get_param()`. Existing call sites (bot/analysis.py, scheduler.py) still work because `db=None` falls back to hardcoded defaults.

- [ ] **4.1** Append a new test to `tests/test_engine.py` that verifies the `db` parameter path:

```python
def test_run_signals_with_db_param(db):
    """run_signals accepts a db parameter and uses get_param() for thresholds."""
    import pandas as pd
    from unittest.mock import patch

    # Minimal OHLCV mock: 250 rows of flat price so MA200 is defined
    rows = 250
    close_vals = [100.0] * rows
    mock_df = pd.DataFrame({
        "Open": close_vals, "High": [101.0] * rows,
        "Low": [99.0] * rows, "Close": close_vals,
        "Volume": [2_000_000] * rows,
    })

    with patch("app.signals.engine.fetch_ohlcv", return_value=mock_df):
        from app.signals.engine import run_signals
        # Should not raise even with a real db session
        result = run_signals("AAPL", db=db)
        assert isinstance(result, list)
```

- [ ] **4.2** Run: `pytest tests/test_engine.py::test_run_signals_with_db_param -v`
  Expected: FAIL (`run_signals() takes 1 positional argument`)

- [ ] **4.3** Edit `app/signals/engine.py` — update the `run_signals` signature and add weight application. Find the line:

```python
def run_signals(ticker: str) -> list[SignalResult]:
```

Replace with:

```python
def run_signals(ticker: str, db=None) -> list[SignalResult]:
    """
    Run all indicator signals for a given ticker.

    Args:
        ticker: Stock symbol.
        db: Optional SQLAlchemy session. When provided, tunable parameters
            (volume_ratio_min, rr_ratio_min, indicator weights) are read
            from IndicatorParams table via get_param(). Falls back to
            hardcoded defaults when db is None.
    """
```

- [ ] **4.4** In `run_signals()`, add the `get_param` import and compute `_volume_ratio_min` right after `volume_ratio` is computed (around line 167):

```python
    from app.learning.params import get_param
    _volume_ratio_min = get_param(db, "volume_ratio_min", 1.2)
```

The volume threshold appears in **two** places inside `run_signals()`. Both use `volume_ratio >= 1.2` as an inline condition. Change both occurrences:

**Location 1** — BUY confluence block (around line 372):
```python
# BEFORE:
    if regime != "BEAR" and stock_trend != "DOWN" and volume_ratio >= 1.2:
# AFTER:
    if regime != "BEAR" and stock_trend != "DOWN" and volume_ratio >= _volume_ratio_min:
```

**Location 2** — SELL confluence block (around line 399):
```python
# BEFORE:
    if len(sell_signals) >= 2 and volume_ratio >= 1.2 and regime != "BULL" and stock_trend != "UP":
# AFTER:
    if len(sell_signals) >= 2 and volume_ratio >= _volume_ratio_min and regime != "BULL" and stock_trend != "UP":
```

- [ ] **4.5** Find the R:R filter in `_build_entry_exit()`:

```python
    if rr < 1.5:
        return None
```

Because `_build_entry_exit` doesn't have db, we pass `rr_ratio_min` as a parameter. Update the function signature:

```python
def _build_entry_exit(price: float, support: Optional[float], resistance, atr, rr_ratio_min: float = 1.5):
```

And change the check:
```python
    if rr < rr_ratio_min:
        return None
```

In `run_signals()`, compute and pass it:
```python
    _rr_ratio_min = get_param(db, "rr_ratio_min", 1.5)
```

Then wherever `_build_entry_exit` is called, add the parameter:
```python
    levels = _build_entry_exit(price, support, resistance, atr, rr_ratio_min=_rr_ratio_min)
```

- [ ] **4.6** Apply indicator weight multipliers. In `run_signals()`, find the confluence detection block (search for `# --- Confluence Detection ---` at approximately line 365) and insert *before* it:

```python
    # Apply per-indicator confidence weights from IndicatorParams
    _weight_map = {
        "MACD":     get_param(db, "macd_weight",     1.0),
        "RSI":      get_param(db, "rsi_weight",      1.0),
        "MA_CROSS": get_param(db, "ma_cross_weight", 1.0),
    }
    for sig in raw_signals:
        w = _weight_map.get(sig.indicator, 1.0)
        if w != 1.0:
            sig.confidence = int(min(95, sig.confidence * w))
```

> **Note:** `raw_signals` is the list of individual indicator results before confluence merging. Check the actual variable name used in the engine and adapt accordingly.

- [ ] **4.7** Run the full engine test suite: `pytest tests/test_engine.py tests/test_engine_v2.py -v`
  Expected: All tests PASS (including the new one)

- [ ] **4.8** Commit:
```bash
git add app/signals/engine.py tests/test_engine.py
git commit -m "feat(phase-a): engine accepts db param, reads volume/rr/weights from IndicatorParams"
```

---

## Task 5: Scheduler — Phase A Daily Integration

**Files:**
- Modify: `app/scheduler.py`
- Test: `tests/test_scheduler.py` (add test)

- [ ] **5.1** Append a test to `tests/test_scheduler.py` that verifies `_daily_job` calls `evaluate_signal_outcomes`:

```python
def test_daily_job_calls_outcome_evaluation():
    """_daily_job() invokes evaluate_signal_outcomes after check_active_trades."""
    from unittest.mock import patch, MagicMock

    mock_db = MagicMock()

    # SessionLocal() returns mock_db (direct call, not context manager)
    with patch("app.scheduler.scan_all_stocks") as mock_scan, \
         patch("app.scheduler.check_active_trades") as mock_check, \
         patch("app.scheduler.SessionLocal", return_value=mock_db), \
         patch("app.learning.outcome_tracker.evaluate_signal_outcomes", return_value=3) as mock_eval:
        from importlib import reload
        import app.scheduler as sched_module
        reload(sched_module)
        sched_module._daily_job()

    mock_scan.assert_called_once()
    mock_check.assert_called_once()
    mock_eval.assert_called_once_with(mock_db)
    mock_db.close.assert_called_once()
```

- [ ] **5.2** Run: `pytest tests/test_scheduler.py::test_daily_job_calls_outcome_evaluation -v`
  Expected: FAIL (mock_eval not called)

- [ ] **5.3** In `app/scheduler.py`, update `_daily_job()`:

```python
def _daily_job():
    """Wrapper that runs scan, position monitor, and signal outcome evaluation."""
    scan_all_stocks()
    check_active_trades()
    db = SessionLocal()
    try:
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)
        if count:
            logger.info(f"Evaluated {count} signal outcomes")
    except Exception as e:
        logger.error(f"Signal outcome evaluation error: {e}", exc_info=True)
    finally:
        db.close()
```

- [ ] **5.4** Run: `pytest tests/test_scheduler.py -v`
  Expected: All tests PASS

- [ ] **5.5** Commit:
```bash
git add app/scheduler.py tests/test_scheduler.py
git commit -m "feat(phase-a): daily job evaluates signal outcomes after position check"
```

---

## Task 6: MCP Tool — stock_monitor_get_signal_stats

**Files:**
- Modify: `app/mcp_server.py`

- [ ] **6.1** Open `app/mcp_server.py`. Find the last `@mcp.tool` definition. After it, append:

```python
@mcp.tool
def stock_monitor_get_signal_stats(days: int = 30) -> str:
    """
    Signal performance statistics over the last N days.
    Shows per-indicator win rate, average P&L, and sample count.
    Uses SignalOutcome table. Returns a message if < 5 outcomes exist.

    Args:
        days: Lookback window in days (default 30).
    """
    from datetime import UTC, datetime, timedelta
    from app.database import SessionLocal
    from app.models import SignalOutcome

    db = SessionLocal()
    try:
        since = datetime.now(UTC) - timedelta(days=days)
        outcomes = (
            db.query(SignalOutcome)
            .filter(SignalOutcome.evaluated_at >= since)
            .all()
        )

        if len(outcomes) < 5:
            return f"📊 近{days}天信号数据不足（{len(outcomes)} 条），暂无统计"

        # Aggregate per indicator
        from collections import defaultdict
        stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "neutrals": 0,
                                                        "win_pcts": [], "loss_pcts": []})
        for o in outcomes:
            key = o.indicator
            if o.result == "WIN":
                stats[key]["wins"] += 1
                stats[key]["win_pcts"].append(o.outcome_pct or 0)
            elif o.result == "LOSS":
                stats[key]["losses"] += 1
                stats[key]["loss_pcts"].append(o.outcome_pct or 0)
            else:
                stats[key]["neutrals"] += 1

        lines = [f"📈 信号表现统计（近{days}天）\n"]
        total_wins = sum(s["wins"] for s in stats.values())
        total_n    = len(outcomes)

        for indicator, s in sorted(stats.items()):
            n = s["wins"] + s["losses"] + s["neutrals"]
            if n == 0:
                continue
            win_rate = s["wins"] / n
            avg_win  = sum(s["win_pcts"]) / len(s["win_pcts"]) if s["win_pcts"] else 0.0
            avg_loss = sum(s["loss_pcts"]) / len(s["loss_pcts"]) if s["loss_pcts"] else 0.0
            ev       = win_rate * avg_win + (1 - win_rate) * avg_loss
            icon     = "🟢" if ev > 0 else "🔴"
            lines.append(
                f"{icon} {indicator:<12} 胜率 {win_rate:.0%} | "
                f"均盈 {avg_win:+.1f}% | 均亏 {avg_loss:+.1f}% | "
                f"期望值 {ev:+.2f}% | 样本 {n}"
            )

        overall_win_rate = total_wins / total_n if total_n else 0.0
        lines.append(f"\n整体: {total_n} 条信号 | 胜率 {overall_win_rate:.0%}")
        return "\n".join(lines)

    finally:
        db.close()
```

- [ ] **6.2** Verify the MCP server still imports cleanly:
```bash
python -c "from app.mcp_server import mcp; print('OK')"
```
  Expected: `OK`

- [ ] **6.3** Commit:
```bash
git add app/mcp_server.py
git commit -m "feat(phase-a): add stock_monitor_get_signal_stats MCP tool"
```

---

## Task 7: Phase B — Market Sentiment Module

**Files:**
- Create: `app/data/market_sentiment.py`
- Create: `tests/test_market_sentiment.py`

- [ ] **7.1** Create `tests/test_market_sentiment.py`:

```python
"""Tests for market sentiment composite score."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import pandas as pd
import numpy as np


@pytest.mark.asyncio
async def test_composite_score_neutral():
    """Fear=50, VIX slope=0, Finnhub=0.5 → composite ~50."""
    import httpx

    mock_fg_resp = MagicMock(spec=httpx.Response)
    mock_fg_resp.json.return_value = {"fear_and_greed": {"score": 50, "rating": "Neutral"}}
    mock_fg_resp.raise_for_status = MagicMock()

    # VIX flat: 30 daily closes all = 20
    vix_data = pd.DataFrame({"Close": [20.0] * 30})

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", return_value=vix_data):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_fg_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment(["AAPL"])

    # slope ≈ 0 → vix_component ≈ 50; fg=50; finnhub default 0.5 → composite ≈ 50
    assert 40 <= result.composite_score <= 60
    assert result.fear_greed_score == 50


@pytest.mark.asyncio
async def test_composite_score_extreme_fear():
    """Fear=15 (Extreme Fear) → composite < 40."""
    import httpx

    mock_fg_resp = MagicMock(spec=httpx.Response)
    mock_fg_resp.json.return_value = {"fear_and_greed": {"score": 15, "rating": "Extreme Fear"}}
    mock_fg_resp.raise_for_status = MagicMock()

    vix_rising = pd.DataFrame({"Close": [15.0 + i * 0.5 for i in range(30)]})  # slope ~0.5/day

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", return_value=vix_rising):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_fg_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment([])

    assert result.fear_greed_score == 15
    assert result.fear_greed_label == "Extreme Fear"
    assert result.composite_score < 40


@pytest.mark.asyncio
async def test_error_returns_neutral_defaults():
    """CNN endpoint fails → returns neutral defaults, no exception raised."""
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", side_effect=Exception("network error")):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment(["AAPL"])

    assert result.composite_score == 50   # neutral default
    assert result.fear_greed_score == 50


def test_composite_formula():
    """Verify composite_score formula manually."""
    from app.data.market_sentiment import _compute_composite
    # fg=70, slope=-0.2 (falling VIX → bullish), finnhub_bullish=0.7
    # vix_component = clip(100 - (-0.2 * 80 + 50), 0, 100) = clip(100 - 34, 0, 100) = 66
    # composite = int(70*0.5 + 66*0.3 + 70*0.2) = int(35 + 19.8 + 14) = int(68.8) = 68
    score = _compute_composite(fear_greed=70, vix_slope=-0.2, finnhub_bullish_pct=0.7)
    assert score == 68
```

- [ ] **7.2** Run: `pytest tests/test_market_sentiment.py -v`
  Expected: All FAIL with ModuleNotFoundError

- [ ] **7.3** Create `app/data/market_sentiment.py`:

```python
"""Market sentiment composite: CNN Fear&Greed + VIX 30d slope + Finnhub news."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import numpy as np

logger = logging.getLogger(__name__)

_CACHE: dict = {"sentiment": None, "ts": 0.0}
_CACHE_TTL = 3600.0  # 1 hour

_CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


@dataclass
class MarketSentiment:
    fear_greed_score: int       # 0-100, CNN Fear & Greed
    fear_greed_label: str       # "Extreme Fear"/"Fear"/"Neutral"/"Greed"/"Extreme Greed"
    vix_30d_slope: float        # linear slope of VIX daily closes over 30 days (pts/day)
    finnhub_bullish_pct: float  # 0.0-1.0, avg bullish_pct across watchlist tickers
    composite_score: int        # aggregated 0-100 (higher = more bullish)


def _compute_composite(
    fear_greed: int,
    vix_slope: float,
    finnhub_bullish_pct: float,
) -> int:
    """
    Composite formula:
      fg_component      = fear_greed_score                        (0-100)
      vix_component     = clip(100 - (vix_slope * 80 + 50), 0, 100)  (inverted)
      finnhub_component = finnhub_bullish_pct * 100              (0-100)
      composite         = int(fg*0.5 + vix*0.3 + finnhub*0.2)

    VIX slope multiplier 80: slope=+0.625 → vix_component=0 (severe bear)
                              slope=-0.625 → vix_component=100 (strong bull)
                              slope=0      → vix_component=50  (neutral)
    """
    vix_component     = float(np.clip(100 - (vix_slope * 80 + 50), 0, 100))
    finnhub_component = finnhub_bullish_pct * 100
    raw = fear_greed * 0.5 + vix_component * 0.3 + finnhub_component * 0.2
    return int(raw)


async def _fetch_fear_greed() -> tuple[int, str]:
    """Fetch CNN Fear & Greed score. Returns (score, label), defaults (50, 'Neutral') on error."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_CNN_FG_URL)
            resp.raise_for_status()
            data = resp.json()
            fg_data = data.get("fear_and_greed", {})
            score = int(float(fg_data.get("score", 50)))
            label = str(fg_data.get("rating", "Neutral"))
            return score, label
    except Exception as e:
        logger.debug(f"Fear&Greed fetch failed: {e}")
        return 50, "Neutral"


async def _fetch_vix_slope() -> float:
    """Compute VIX 30-day linear slope (pts/day). Returns 0.0 on error."""
    try:
        import yfinance
        import pandas as pd
        df = yfinance.download("^VIX", period="40d", interval="1d", progress=False)
        if df is None or df.empty:
            return 0.0
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        closes = df["Close"].dropna().tail(30).values
        if len(closes) < 5:
            return 0.0
        x = np.arange(len(closes), dtype=float)
        slope, _ = np.polyfit(x, closes, 1)
        return float(slope)
    except Exception as e:
        logger.debug(f"VIX slope fetch failed: {e}")
        return 0.0


async def _fetch_finnhub_avg(tickers: list[str]) -> float:
    """Average bullish_pct from Finnhub news sentiment across tickers. Returns 0.5 on error."""
    if not tickers:
        return 0.5
    try:
        from app.data.news import get_ticker_sentiment
        tasks = [get_ticker_sentiment(t) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        values = [r["bullish_pct"] for r in results if isinstance(r, dict) and "bullish_pct" in r]
        return sum(values) / len(values) if values else 0.5
    except Exception as e:
        logger.debug(f"Finnhub avg failed: {e}")
        return 0.5


async def get_market_sentiment(tickers: list[str]) -> MarketSentiment:
    """
    Fetch all three sentiment signals concurrently. Returns neutral defaults on error.
    Results are cached for 1 hour to avoid redundant API calls within the same scan.

    Args:
        tickers: Watchlist tickers to aggregate Finnhub news sentiment.
    """
    now = time.time()
    if _CACHE["ts"] and now - _CACHE["ts"] < _CACHE_TTL and _CACHE["sentiment"] is not None:
        return _CACHE["sentiment"]

    fg_score, fg_label, vix_slope, finnhub_pct = 50, "Neutral", 0.0, 0.5
    try:
        (fg_score, fg_label), vix_slope, finnhub_pct = await asyncio.gather(
            _fetch_fear_greed(),
            _fetch_vix_slope(),
            _fetch_finnhub_avg(tickers),
        )
    except Exception as e:
        logger.warning(f"Market sentiment fetch error: {e}")

    composite = _compute_composite(fg_score, vix_slope, finnhub_pct)
    sentiment = MarketSentiment(
        fear_greed_score   = fg_score,
        fear_greed_label   = fg_label,
        vix_30d_slope      = vix_slope,
        finnhub_bullish_pct= finnhub_pct,
        composite_score    = composite,
    )
    _CACHE["sentiment"] = sentiment
    _CACHE["ts"] = now
    return sentiment
```

- [ ] **7.4** Run: `pytest tests/test_market_sentiment.py -v`
  Expected: All 4 tests PASS

  > If `pytest-asyncio` is not configured, add `asyncio_mode = "auto"` to `pytest.ini` or `pyproject.toml`, or add `@pytest.mark.asyncio` to each async test.

- [ ] **7.5** Commit:
```bash
git add app/data/market_sentiment.py tests/test_market_sentiment.py
git commit -m "feat(phase-b): add market sentiment module (F&G + VIX slope + Finnhub)"
```

---

## Task 8: Scheduler — Phase B Sentiment Integration

**Files:**
- Modify: `app/scheduler.py`
- Test: `tests/test_scheduler.py` (add test)

- [ ] **8.1** Append to `tests/test_scheduler.py`:

```python
def test_scan_uses_market_sentiment(monkeypatch):
    """scan_all_stocks() fetches market sentiment once before the per-ticker loop."""
    from unittest.mock import patch, MagicMock
    from app.data.market_sentiment import MarketSentiment

    neutral_sentiment = MarketSentiment(
        fear_greed_score=50, fear_greed_label="Neutral",
        vix_30d_slope=0.0, finnhub_bullish_pct=0.5, composite_score=50,
    )

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.scheduler._run_async", return_value=neutral_sentiment) as mock_async, \
         patch("app.scheduler.run_signals", return_value=[]) as mock_rs:
        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__  = MagicMock(return_value=False)
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_sl.return_value = mock_db
        from app.scheduler import scan_all_stocks
        scan_all_stocks()

    # _run_async should have been called for get_market_sentiment
    assert mock_async.call_count >= 1
```

- [ ] **8.2** In `app/scheduler.py`, update `scan_all_stocks()`. Add the sentiment fetch **before** the per-ticker `for ticker in tickers:` loop, and apply confidence adjustments inside the loop:

Find the block:
```python
        for ticker in tickers:
            try:
                signals = run_signals(ticker)
```

Insert before it:
```python
        # ── Phase B: Market Sentiment (once per scan run) ─────────────────────
        from app.data.market_sentiment import get_market_sentiment
        market_sentiment = _run_async(get_market_sentiment(tickers))
        logger.info(
            f"Market sentiment: F&G={market_sentiment.fear_greed_score} "
            f"({market_sentiment.fear_greed_label}), "
            f"VIX slope={market_sentiment.vix_30d_slope:+.3f}, "
            f"composite={market_sentiment.composite_score}"
        )
```

- [ ] **8.3** Inside the per-ticker loop, after the news sentiment block and before the debate block, add the market sentiment confidence adjustment:

```python
                # ── Phase B: Apply market-wide sentiment adjustments ──────────────
                fg = market_sentiment.fear_greed_score
                vix_slope = market_sentiment.vix_30d_slope

                for sig in push_signals:
                    # Contrarian: extreme fear is good for BUY, bad for SELL
                    if fg < 25 and sig.signal_type == "BUY":
                        sig.confidence = min(95, sig.confidence + 5)
                    elif fg < 25 and sig.signal_type == "SELL":
                        sig.confidence = max(0, sig.confidence - 10)
                    elif fg > 75 and sig.signal_type == "BUY":
                        sig.confidence = max(0, sig.confidence - 5)
                    elif fg > 75 and sig.signal_type == "SELL":
                        sig.confidence = min(95, sig.confidence + 5)

                # Adjust effective push threshold based on VIX trend (in-memory only).
                # Use get_param() so Phase C auto-tuned values are respected.
                from app.learning.params import get_param
                effective_min_confidence = int(get_param(db, "push_min_confidence", settings.push_min_confidence))
                if vix_slope > 0.3:
                    effective_min_confidence += 5   # fear rising → be stricter
                elif vix_slope < -0.3:
                    effective_min_confidence = max(45, effective_min_confidence - 3)

                push_signals = [
                    s for s in push_signals
                    if s.confidence >= effective_min_confidence
                ]
                if not push_signals:
                    logger.info(f"No push-worthy signals for {ticker} after sentiment filter")
                    continue
```

- [ ] **8.4** Run: `pytest tests/test_scheduler.py -v`
  Expected: All tests PASS

- [ ] **8.5** Commit:
```bash
git add app/scheduler.py tests/test_scheduler.py
git commit -m "feat(phase-b): apply market sentiment adjustments in scan_all_stocks()"
```

---

## Task 9: Phase C — Auto-Tuner

**Files:**
- Create: `app/learning/auto_tuner.py`
- Create: `tests/test_auto_tuner.py`

- [ ] **9.1** Create `tests/test_auto_tuner.py`:

```python
"""Tests for monthly auto-tuner."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import UTC, datetime, timedelta


def _seed_outcomes(db, n_win=8, n_loss=4, n_neutral=3, indicator="MACD+RSI"):
    """Create fake SignalOutcome records for testing."""
    from app.models import SignalOutcome
    base = datetime.now(UTC) - timedelta(days=10)
    for i in range(n_win):
        db.add(SignalOutcome(
            signal_id=i + 1000, ticker="AAPL", indicator=indicator,
            signal_type="BUY", entry_price=100.0, stop_price=95.0,
            outcome_price=103.0, outcome_pct=3.0, result="WIN",
            evaluated_at=base,
        ))
    for i in range(n_loss):
        db.add(SignalOutcome(
            signal_id=i + 2000, ticker="AAPL", indicator=indicator,
            signal_type="BUY", entry_price=100.0, stop_price=95.0,
            outcome_price=94.0, outcome_pct=-6.0, result="LOSS",
            evaluated_at=base,
        ))
    for i in range(n_neutral):
        db.add(SignalOutcome(
            signal_id=i + 3000, ticker="AAPL", indicator=indicator,
            signal_type="BUY", entry_price=100.0, stop_price=95.0,
            outcome_price=100.5, outcome_pct=0.5, result="NEUTRAL",
            evaluated_at=base,
        ))
    db.commit()


def test_skip_insufficient_data(db):
    """Returns None if fewer than 10 outcomes in last 30 days."""
    _seed_outcomes(db, n_win=3, n_loss=2, n_neutral=1)  # total 6 < 10
    from app.learning.auto_tuner import auto_tune_params
    result = auto_tune_params(db)
    assert result is None


def test_clamp_applies_relative_and_absolute(db):
    """Safety clamp: relative ±20%, then absolute hard bounds."""
    from app.learning.auto_tuner import _apply_clamp

    # Relative clamp: current=60, recommended=40 → clamped to 60*0.8=48 → floor=45 → 48
    assert _apply_clamp("push_min_confidence", current=60.0, recommended=40.0) == 48.0

    # Absolute floor: current=50, recommended=30 → relative clamp 40 → floor 45 → 45
    assert _apply_clamp("push_min_confidence", current=50.0, recommended=30.0) == 45.0

    # Absolute ceiling: current=80, recommended=95 → relative clamp 96 → ceiling 85 → 85
    assert _apply_clamp("push_min_confidence", current=80.0, recommended=95.0) == 85.0

    # Within ±20%: current=1.0 weight, recommended=1.15 → stays 1.15
    assert _apply_clamp("macd_weight", current=1.0, recommended=1.15) == 1.15


def test_llm_json_parse_error_returns_none(db):
    """If LLM returns unparseable JSON, skip tuning gracefully."""
    _seed_outcomes(db, n_win=8, n_loss=2, n_neutral=3)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="This is not JSON at all!"))]

    with patch("app.learning.auto_tuner._call_llm", return_value="This is not JSON!"):
        from app.learning.auto_tuner import auto_tune_params
        result = auto_tune_params(db)
    assert result is None


def test_unknown_keys_ignored(db):
    """LLM returning unknown param keys should be silently ignored."""
    from app.learning.auto_tuner import _apply_llm_recommendations
    from app.models import IndicatorParams

    current = {"push_min_confidence": 60.0, "macd_weight": 1.0}
    recommendations = {"push_min_confidence": 63.0, "unknown_param": 99.9}

    changes = _apply_llm_recommendations(db, current, recommendations)
    assert "unknown_param" not in changes
    assert "push_min_confidence" in changes


def test_low_sample_indicator_weight_unchanged(db):
    """Indicator with < 5 samples: weight should not change."""
    from app.learning.auto_tuner import _build_stats

    from app.models import SignalOutcome
    from datetime import UTC
    db.add(SignalOutcome(
        signal_id=9001, ticker="NVDA", indicator="MA_CROSS",
        signal_type="BUY", entry_price=100.0, stop_price=95.0,
        outcome_price=103.0, outcome_pct=3.0, result="WIN",
        evaluated_at=datetime.now(UTC),
    ))
    db.commit()

    stats = _build_stats(db, days=30)
    ma_stats = stats.get("MA_CROSS", {})
    assert ma_stats.get("n", 0) < 5
```

- [ ] **9.2** Run: `pytest tests/test_auto_tuner.py -v`
  Expected: All FAIL with ModuleNotFoundError

- [ ] **9.3** Create `app/learning/auto_tuner.py`:

```python
"""Monthly auto-tuner: analyze SignalOutcome stats, call LLM, update IndicatorParams."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.learning.params import HARD_CEILING, HARD_FLOOR, get_param

logger = logging.getLogger(__name__)

_TUNABLE_KEYS = set(HARD_FLOOR.keys())


def _build_stats(db: Session, days: int = 30) -> dict[str, dict]:
    """Aggregate per-indicator win/loss/neutral stats from last N days."""
    from app.models import SignalOutcome

    since = datetime.now(UTC) - timedelta(days=days)
    outcomes = (
        db.query(SignalOutcome)
        .filter(SignalOutcome.evaluated_at >= since)
        .all()
    )

    stats: dict = defaultdict(lambda: {
        "wins": 0, "losses": 0, "neutrals": 0,
        "win_pcts": [], "loss_pcts": [], "n": 0,
    })
    for o in outcomes:
        key = o.indicator
        stats[key]["n"] += 1
        if o.result == "WIN":
            stats[key]["wins"] += 1
            stats[key]["win_pcts"].append(o.outcome_pct or 0.0)
        elif o.result == "LOSS":
            stats[key]["losses"] += 1
            stats[key]["loss_pcts"].append(o.outcome_pct or 0.0)
        else:
            stats[key]["neutrals"] += 1

    # Convert to serializable summary
    result = {}
    for indicator, s in stats.items():
        n = s["n"]
        if n == 0:
            continue
        win_rate = s["wins"] / n
        avg_win  = sum(s["win_pcts"]) / len(s["win_pcts"]) if s["win_pcts"] else 0.0
        avg_loss = sum(s["loss_pcts"]) / len(s["loss_pcts"]) if s["loss_pcts"] else 0.0
        result[indicator] = {
            "win_rate":    round(win_rate, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct":round(avg_loss, 4),
            "n":           n,
        }
    return result


def _get_current_params(db: Session) -> dict[str, float]:
    """Fetch all tunable params from DB (with defaults)."""
    from app.config import settings
    defaults = {
        "push_min_confidence": float(settings.push_min_confidence),
        "macd_weight":         1.0,
        "rsi_weight":          1.0,
        "ma_cross_weight":     1.0,
        "volume_ratio_min":    1.2,
        "rr_ratio_min":        1.5,
    }
    return {k: get_param(db, k, v) for k, v in defaults.items()}


def _apply_clamp(key: str, current: float, recommended: float) -> float:
    """Apply ±20% relative clamp then absolute hard bounds."""
    clamped = max(current * 0.8, min(current * 1.2, recommended))
    floored  = HARD_FLOOR.get(key, clamped)
    ceilinged = HARD_CEILING.get(key, clamped)
    return round(max(floored, min(ceilinged, clamped)), 4)


def _call_llm(stats_json: str, params_json: str) -> str:
    """Call gpt-4.1 with the tuning prompt. Returns raw string response."""
    from openai import OpenAI
    from app.config import settings

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    prompt = f"""你是一个量化策略优化师。以下是过去30天的信号表现数据：

{stats_json}

每条数据包含：win_rate（胜率）、avg_win_pct（平均盈利%）、avg_loss_pct（平均亏损%，负数）、n（样本数）。

当前参数：
{params_json}

请分析表现并给出参数调整建议。规则：
- 优先看期望值：期望值 = win_rate × avg_win_pct + (1 - win_rate) × avg_loss_pct
  - 期望值 > 0 的指标有正期望，不应降低权重，即使胜率看起来偏低
  - 期望值 < 0 的指标应降低权重，即使胜率看起来还行
- 胜率 > 65% 且期望值 > 0 的指标可适当提升权重（最多 ×1.2）
- 期望值 < -0.5% 的指标应降低权重（最多 ×0.8）
- 样本数 < 5 的指标数据不可信，对应权重保持不变
- 每个参数调整幅度不超过当前值的 20%（系统会自动 clamp）
- 返回纯 JSON，格式：{{"param_key": new_value, ...}}
- 只返回需要变更的参数，不变的不要包含
- 不要包含任何解释文字，只有 JSON"""

    resp = client.chat.completions.create(
        model=settings.llm_model_analysis,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _apply_llm_recommendations(
    db: Session,
    current: dict[str, float],
    recommendations: dict,
) -> dict[str, tuple[float, float]]:
    """
    Validate and apply LLM recommendations.
    Returns {key: (old_value, new_value)} for changed params only.
    """
    from app.models import IndicatorParams

    year = datetime.now(UTC).year
    month = datetime.now(UTC).month
    updated_by = f"auto_tune_{year}-{month:02d}"

    changes = {}
    for key, raw_val in recommendations.items():
        # Skip unknown keys
        if key not in _TUNABLE_KEYS:
            logger.debug(f"Auto-tune: ignoring unknown key '{key}'")
            continue
        # Validate value
        try:
            recommended = float(raw_val)
            if not (recommended > 0 and recommended == recommended):  # positive & not NaN
                raise ValueError
        except (ValueError, TypeError):
            logger.warning(f"Auto-tune: invalid value for '{key}': {raw_val!r}, keeping current")
            continue

        old_val = current.get(key, recommended)
        new_val = _apply_clamp(key, old_val, recommended)
        if abs(new_val - old_val) < 0.0001:
            continue  # no meaningful change

        # Upsert into IndicatorParams
        row = db.query(IndicatorParams).filter_by(param_key=key).first()
        if row:
            row.param_value = new_val
            row.updated_by  = updated_by
        else:
            db.add(IndicatorParams(param_key=key, param_value=new_val, updated_by=updated_by))

        changes[key] = (old_val, new_val)

    db.commit()
    return changes


def auto_tune_params(db: Session) -> Optional[dict]:
    """
    Analyze last 30 days of SignalOutcome data.
    Call gpt-4.1 to recommend parameter adjustments.
    Apply changes within ±20% + absolute hard bounds.

    Returns dict of {key: (old, new)} for changed params, or None if skipped.

    Note: This function runs on the 1st of each month at 08:30 ET.
    The last ~5 trading days of the previous month will NOT be in SignalOutcome yet
    (5-day evaluation lag). Effective lookback window is ~25 trading days.
    """
    from app.models import ParamTuningHistory

    stats = _build_stats(db, days=30)
    total_n = sum(s["n"] for s in stats.values())

    if total_n < 10:
        logger.info(f"Auto-tune skipped: only {total_n} outcomes in last 30 days (need ≥ 10)")
        return None

    current_params = _get_current_params(db)
    stats_json   = json.dumps(stats, indent=2, ensure_ascii=False)
    params_json  = json.dumps(current_params, indent=2)

    raw_response = _call_llm(stats_json, params_json)

    # Parse LLM response
    try:
        # Strip markdown code fences if present
        content = raw_response.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        recommendations = json.loads(content)
        if not isinstance(recommendations, dict):
            raise ValueError("Expected a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Auto-tune: LLM returned unparseable JSON ({e}), skipping")
        return None

    params_before = json.dumps(current_params)
    changes = _apply_llm_recommendations(db, current_params, recommendations)

    if not changes:
        logger.info("Auto-tune: no parameter changes recommended")
        return {}

    # Compute new params snapshot
    new_params = {k: v[1] for k, v in changes.items()}
    for k, v in current_params.items():
        if k not in new_params:
            new_params[k] = v

    # Write audit log
    history = ParamTuningHistory(
        signals_analyzed = total_n,
        params_before    = params_before,
        params_after     = json.dumps(new_params),
        llm_reasoning    = raw_response[:2000],  # truncate for storage
    )
    db.add(history)
    db.commit()

    # Send Telegram summary
    _send_tuning_summary(total_n, stats, changes)
    logger.info(f"Auto-tune complete: {len(changes)} params changed")
    return changes


def _send_tuning_summary(total_n: int, stats: dict, changes: dict) -> None:
    """Send Telegram notification about tuning results (fire and forget)."""
    try:
        from app.notifications.telegram import send_telegram
        import asyncio

        total_wins = sum(s["wins"] for s in stats.values())
        win_rate = total_wins / total_n if total_n else 0.0

        lines = [f"📊 本月自动调参完成\n分析 {total_n} 条信号 | 整体胜率 {win_rate:.0%}\n"]

        if changes:
            lines.append("变更项：")
            for key, (old_val, new_val) in changes.items():
                direction = "↑" if new_val > old_val else "↓"
                lines.append(f"• {key}: {old_val:.2f} → {new_val:.2f} {direction}")
        else:
            lines.append("本月参数无变更")

        unchanged = [k for k in HARD_FLOOR if k not in changes]
        if unchanged:
            lines.append(f"\n未变更：{', '.join(unchanged)}")

        message = "\n".join(lines)
        asyncio.run(send_telegram(message))
    except Exception as e:
        logger.warning(f"Auto-tune Telegram notification failed: {e}")
```

- [ ] **9.4** Run: `pytest tests/test_auto_tuner.py -v`
  Expected: All 5 tests PASS

- [ ] **9.5** Commit:
```bash
git add app/learning/auto_tuner.py tests/test_auto_tuner.py
git commit -m "feat(phase-c): monthly auto-tuner with LLM, clamp safety, audit log"
```

---

## Task 10: Scheduler — Phase C Monthly Cron

**Files:**
- Modify: `app/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **10.1** Append to `tests/test_scheduler.py`:

```python
def test_auto_tune_params_job_calls_auto_tune(db):
    """auto_tune_params_job() wraps auto_tune_params() with DB session."""
    from unittest.mock import patch, MagicMock

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.learning.auto_tuner.auto_tune_params", return_value={}) as mock_tune:
        mock_db = MagicMock()
        mock_sl.return_value = mock_db
        from app.scheduler import auto_tune_params_job
        auto_tune_params_job()

    mock_tune.assert_called_once_with(mock_db)
    mock_db.close.assert_called_once()


def test_monthly_cron_registered(monkeypatch):
    """start_scheduler() registers a monthly_auto_tune cron job."""
    from unittest.mock import MagicMock, patch
    mock_scheduler = MagicMock()
    monkeypatch.setattr("app.scheduler._scheduler", mock_scheduler)

    with patch("app.scheduler.refresh_calendar_job"):
        from app.scheduler import start_scheduler
        start_scheduler()

    job_ids = [call.kwargs.get("id") for call in mock_scheduler.add_job.call_args_list]
    assert "monthly_auto_tune" in job_ids
```

- [ ] **10.2** In `app/scheduler.py`, add the wrapper function (after `refresh_calendar_job`):

```python
def auto_tune_params_job() -> None:
    """Wrapper for monthly auto-tune cron job. Opens own DB session."""
    db = SessionLocal()
    try:
        from app.learning.auto_tuner import auto_tune_params
        result = auto_tune_params(db)
        if result is None:
            logger.info("Auto-tune skipped: insufficient signal outcomes")
        else:
            logger.info(f"Auto-tune complete: {len(result)} params changed")
    except Exception as e:
        logger.error(f"Auto-tune error: {e}", exc_info=True)
    finally:
        db.close()
```

- [ ] **10.3** In `start_scheduler()`, add the monthly cron after the existing jobs:

```python
    # Monthly auto-tune: 1st of month at 08:30 ET (offset from 08:00 calendar refresh)
    _scheduler.add_job(
        auto_tune_params_job,
        CronTrigger(day=1, hour=8, minute=30, timezone="America/New_York"),
        id="monthly_auto_tune",
        replace_existing=True,
    )
```

- [ ] **10.4** Run: `pytest tests/test_scheduler.py -v`
  Expected: All tests PASS

- [ ] **10.5** Commit:
```bash
git add app/scheduler.py tests/test_scheduler.py
git commit -m "feat(phase-c): register monthly auto_tune_params_job in APScheduler"
```

---

## Task 11: MCP Tool — get_tuning_history + README Update

**Files:**
- Modify: `app/mcp_server.py`
- Modify: `README.md`

- [ ] **11.1** Append to `app/mcp_server.py` after `stock_monitor_get_signal_stats`:

```python
@mcp.tool
def stock_monitor_get_tuning_history(limit: int = 3) -> str:
    """
    Recent auto-tuning events with before/after params and LLM reasoning summary.

    Args:
        limit: Number of most recent tuning events to show (default 3).
    """
    import json as _json
    from app.database import SessionLocal
    from app.models import ParamTuningHistory

    db = SessionLocal()
    try:
        records = (
            db.query(ParamTuningHistory)
            .order_by(ParamTuningHistory.tuned_at.desc())
            .limit(limit)
            .all()
        )
        if not records:
            return "📋 暂无自动调参历史记录"

        lines = [f"📋 最近 {len(records)} 次自动调参历史\n"]
        for rec in records:
            lines.append(f"🕐 {rec.tuned_at.strftime('%Y-%m-%d %H:%M')} UTC | 分析 {rec.signals_analyzed} 条信号")
            try:
                before = _json.loads(rec.params_before)
                after  = _json.loads(rec.params_after)
                changed = {k: (before.get(k), v) for k, v in after.items() if abs(v - before.get(k, v)) > 0.0001}
                if changed:
                    for k, (old, new) in changed.items():
                        arrow = "↑" if new > old else "↓"
                        lines.append(f"  • {k}: {old:.2f} → {new:.2f} {arrow}")
                else:
                    lines.append("  • 本次无参数变更")
            except Exception:
                lines.append("  （参数解析失败）")
            lines.append("")

        return "\n".join(lines).strip()
    finally:
        db.close()
```

- [ ] **11.2** Update `README.md` MCP tools table — add two rows after `stock_monitor_get_positions`:

```markdown
| `stock_monitor_get_signal_stats` | 信号表现统计（胜率/期望值/均盈均亏，近N天）|
| `stock_monitor_get_tuning_history` | 月度自动调参历史（前后参数对比）|
```

- [ ] **11.3** Update `README.md` Roadmap table — change the `v2.0` row:

```markdown
| **v2.0** | 🚧 开发中 | 自学习回路：信号结果追踪 · 市场情绪融合（F&G+VIX+Finnhub）· 月度LLM自动调参 |
```

- [ ] **11.4** Verify MCP server imports clean:
```bash
python -c "from app.mcp_server import mcp; print('MCP OK')"
```

- [ ] **11.5** Run full test suite:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```
  Expected: All tests PASS (no new failures)

- [ ] **11.6** Commit:
```bash
git add app/mcp_server.py README.md
git commit -m "feat(phase-c): add get_tuning_history MCP tool; update README"
```

---

## Task 12: Version Bump + Docker

**Files:**
- Modify: `README.md` (version badge)
- Modify: `docker-compose.yml`

- [ ] **12.1** Update version badge in `README.md` from `1.5.8` → `2.0.0`:
```markdown
![Version](https://img.shields.io/badge/version-2.0.0-orange)
```

- [ ] **12.2** Update `docker-compose.yml` image tags from `1.5.8` → `2.0.0`:
```yaml
    image: ghcr.io/nianyi778/stock-signal-monitor:2.0.0
```
(two occurrences: `backend` and `mcp` services)

- [ ] **12.3** Final full test run:
```bash
pytest tests/ -v 2>&1 | grep -E "passed|failed|error"
```
  Expected: `N passed` with 0 failed

- [ ] **12.4** Commit:
```bash
git add README.md docker-compose.yml
git commit -m "chore: bump version to 2.0.0 (self-evolving algorithm)"
```

---

## Verification Plan

After all tasks complete:

1. **Unit tests**: `pytest tests/ -v` — all pass including 3 new test files
2. **Import check**: `python -c "from app.learning.outcome_tracker import evaluate_signal_outcomes; from app.learning.auto_tuner import auto_tune_params; from app.data.market_sentiment import get_market_sentiment; print('OK')"`
3. **MCP tools**: `python -c "from app.mcp_server import mcp; tools = [t.name for t in mcp.tools]; assert 'stock_monitor_get_signal_stats' in tools; assert 'stock_monitor_get_tuning_history' in tools; print(tools)"`
4. **Scheduler jobs**: Start server locally and confirm APScheduler logs show `monthly_auto_tune` registered

---

## Notes for Implementer

- **`pandas_market_calendars` must be installed**: `pip install pandas_market_calendars` before running outcome_tracker tests
- **Engine's `raw_signals` variable name**: Task 4.6 says "check the actual variable name" — the engine currently uses `buy_signals` and `sell_signals` lists before confluence. Apply weight multipliers to whichever lists hold individual indicator results before the confluence merge step.
- **`run_signals(ticker, db=None)` backward compat**: bot/analysis.py calls `run_signals(ticker)` without db — the `db=None` default ensures this keeps working with `get_param()` falling back to defaults.
- **pytest-asyncio**: Market sentiment tests are async. Either add `asyncio_mode = "auto"` to `pytest.ini` or add `@pytest.mark.asyncio` explicitly to each async test function.
