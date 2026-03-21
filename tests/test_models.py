from datetime import datetime

from app.models import Signal, WatchlistItem


def test_create_watchlist_item(db):
    item = WatchlistItem(ticker="AAPL", name="Apple Inc.")
    db.add(item)
    db.commit()
    db.refresh(item)

    assert item.id is not None
    assert item.ticker == "AAPL"
    assert item.name == "Apple Inc."
    assert item.is_active is True
    assert isinstance(item.created_at, datetime)
    assert item.user_id is None


def test_create_signal(db):
    signal = Signal(
        ticker="AAPL",
        signal_type="BUY",
        indicator="MACD+RSI",
        price=175.50,
        target_price=190.00,
        message="MACD bullish crossover with RSI confirmation",
        confidence=80,
        signal_level="STRONG",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    assert signal.id is not None
    assert signal.ticker == "AAPL"
    assert signal.signal_type == "BUY"
    assert signal.indicator == "MACD+RSI"
    assert signal.price == 175.50
    assert signal.target_price == 190.00
    assert signal.confidence == 80
    assert signal.signal_level == "STRONG"
    assert signal.pushed is False
    assert isinstance(signal.triggered_at, datetime)
    assert signal.user_id is None


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
