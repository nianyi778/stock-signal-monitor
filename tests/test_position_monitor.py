# tests/test_position_monitor.py
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ActiveTrade


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@patch("app.scheduler._run_async")
def test_stop_triggered(mock_async, db):
    trade = ActiveTrade(
        ticker="NVDA", entry_low=870.0, entry_high=886.0,
        target_price=950.0, stop_price=844.0, warn_price=851.0,
        partial_tp=902.5, rr_ratio=2.1, status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"NVDA": 840.0})

    db.refresh(trade)
    assert trade.status == "STOPPED"
    assert mock_async.called


@patch("app.scheduler._run_async")
def test_target_triggered(mock_async, db):
    trade = ActiveTrade(
        ticker="AAPL", entry_low=200.0, entry_high=205.0,
        target_price=230.0, stop_price=190.0, warn_price=193.0,
        partial_tp=218.5, rr_ratio=2.0, status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"AAPL": 232.0})

    db.refresh(trade)
    assert trade.status == "TARGET_HIT"


@patch("app.scheduler._run_async")
def test_expiry(mock_async, db):
    trade = ActiveTrade(
        ticker="TSLA", entry_low=200.0, entry_high=210.0,
        target_price=250.0, stop_price=185.0, warn_price=190.0,
        partial_tp=237.5, rr_ratio=2.5, status="ACTIVE",
        valid_until=datetime.now(UTC) - timedelta(days=1),  # already expired
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"TSLA": 220.0})

    db.refresh(trade)
    assert trade.status == "EXPIRED"
