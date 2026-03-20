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
