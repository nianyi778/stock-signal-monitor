"""Tests for signal outcome evaluation logic."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd

# Ensure all models are registered with Base.metadata before any db fixture runs
import app.models  # noqa: F401


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
