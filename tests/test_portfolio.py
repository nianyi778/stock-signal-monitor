from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import PositionEntry


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_add_position(db):
    from app.bot.portfolio import add_position, get_positions_summary
    add_position(db, "NVDA", 882.5, 20.0)
    add_position(db, "NVDA", 300.0, 10.0)
    summary = get_positions_summary(db, "NVDA", current_price=910.0)
    assert summary["ticker"] == "NVDA"
    assert summary["total_shares"] == 30.0
    # weighted avg: (882.5*20 + 300.0*10) / 30 = 688.33
    assert abs(summary["avg_price"] - 688.33) < 0.1
    assert summary["current_pnl_pct"] > 0


def test_sell_position(db):
    from app.bot.portfolio import add_position, sell_position
    add_position(db, "NVDA", 100.0, 10.0)
    result = sell_position(db, "NVDA", 120.0)
    assert result["pnl_pct"] == pytest.approx(20.0)
    assert result["pnl_usd"] == pytest.approx(200.0)


def test_get_all_positions(db):
    from app.bot.portfolio import add_position, get_all_positions_raw
    add_position(db, "NVDA", 882.5, 20.0)
    add_position(db, "AAPL", 200.0, 5.0)
    positions = get_all_positions_raw(db)
    tickers = [p["ticker"] for p in positions]
    assert "NVDA" in tickers
    assert "AAPL" in tickers


def test_format_portfolio_message():
    from app.bot.portfolio import format_portfolio_message
    positions = [
        {"ticker": "NVDA", "total_shares": 30.0, "avg_price": 233.33,
         "current_price": 910.0, "current_pnl_pct": 291.0, "current_pnl_usd": 20300.0,
         "position_pct": 27.3},
    ]
    msg = format_portfolio_message(positions, portfolio_value=100_000.0)
    assert "NVDA" in msg
    assert "30" in msg
