from datetime import UTC, datetime, timedelta
import pytest

from app.models import ActiveTrade, PositionEntry


def test_create_active_trade(db):
    trade = ActiveTrade(
        ticker="NVDA",
        signal_id=1,
        entry_low=870.0,
        entry_high=886.0,
        target_price=950.0,
        stop_price=844.0,
        warn_price=851.0,
        partial_tp=902.5,
        rr_ratio=2.1,
        atr_at_signal=12.5,
        volume_ratio=1.4,
        regime_state="BULL",
        status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade)
    db.commit()
    assert trade.id is not None
    assert trade.status == "ACTIVE"


def test_create_position_entry(db):
    entry = PositionEntry(
        ticker="NVDA",
        buy_price=882.5,
        shares=20.0,
        note="第一笔",
    )
    db.add(entry)
    db.commit()
    assert entry.id is not None


def test_position_entry_multiple_weighted_avg(db):
    for price, qty in [(200.0, 20), (300.0, 10)]:
        db.add(PositionEntry(ticker="NVDA", buy_price=price, shares=qty))
    db.commit()
    entries = db.query(PositionEntry).filter_by(ticker="NVDA", is_active=True).all()
    assert len(entries) == 2
    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    assert total_shares == 30.0
    assert abs(avg_price - 233.33) < 0.1
