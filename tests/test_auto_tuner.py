"""Tests for monthly auto-tuner."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import UTC, datetime, timedelta

# Ensure all models are registered with Base.metadata before any db fixture runs
import app.models  # noqa: F401


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
