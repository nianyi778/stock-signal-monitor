"""Tests for FastAPI REST API endpoints."""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# Use StaticPool so the same in-memory connection is shared across threads
# (required for SQLite + TestClient which may use different threads)
# ---------------------------------------------------------------------------
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create all tables before each test, drop them after."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture
def db_session():
    """Provide a transactional session for the test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Return a TestClient that uses the in-memory test DB."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------


def test_list_stocks_empty(client):
    resp = client.get("/api/stocks/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_add_stock(client):
    mock_ticker = MagicMock()
    mock_ticker.info = {"shortName": "Apple Inc."}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        resp = client.post("/api/stocks/", json={"ticker": "AAPL"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["ticker"] == "AAPL"
    assert data["is_active"] is True


def test_add_stock_with_name_skips_yfinance(client):
    """When a name is provided yfinance should not be called."""
    with patch("yfinance.Ticker") as mock_yf:
        resp = client.post("/api/stocks/", json={"ticker": "TSLA", "name": "Tesla"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["ticker"] == "TSLA"
    assert data["name"] == "Tesla"
    mock_yf.assert_not_called()


def test_add_duplicate_stock(client):
    mock_ticker = MagicMock()
    mock_ticker.info = {"shortName": "Apple Inc."}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        client.post("/api/stocks/", json={"ticker": "AAPL"})
        resp = client.post("/api/stocks/", json={"ticker": "AAPL"})
    # Second attempt should return 409
    assert resp.status_code == 409


def test_delete_stock(client):
    mock_ticker = MagicMock()
    mock_ticker.info = {"shortName": "Apple Inc."}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        client.post("/api/stocks/", json={"ticker": "AAPL"})
    resp = client.delete("/api/stocks/AAPL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["ticker"] == "AAPL"


def test_delete_stock_not_found(client):
    resp = client.delete("/api/stocks/NOTEXIST")
    assert resp.status_code == 404


def test_delete_stock_removes_from_list(client):
    """After soft-delete the ticker should not appear in GET /api/stocks/."""
    mock_ticker = MagicMock()
    mock_ticker.info = {"shortName": "Apple Inc."}
    with patch("yfinance.Ticker", return_value=mock_ticker):
        client.post("/api/stocks/", json={"ticker": "AAPL"})
    client.delete("/api/stocks/AAPL")
    resp = client.get("/api/stocks/")
    assert resp.status_code == 200
    tickers = [s["ticker"] for s in resp.json()]
    assert "AAPL" not in tickers


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_list_signals_empty(client):
    resp = client.get("/api/signals/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_signals_with_data(client, db_session):
    from datetime import UTC, datetime

    from app.models import Signal

    sig = Signal(
        ticker="AAPL",
        signal_type="BUY",
        indicator="RSI",
        price=150.0,
        message="RSI oversold",
        confidence=75,
        signal_level="STRONG",
        triggered_at=datetime.now(UTC),
    )
    db_session.add(sig)
    db_session.commit()

    resp = client.get("/api/signals/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "AAPL"
    assert data[0]["signal_level"] == "STRONG"


def test_list_signals_level_filter(client, db_session):
    from datetime import UTC, datetime

    from app.models import Signal

    for level in ("STRONG", "WEAK", "WATCH"):
        sig = Signal(
            ticker="MSFT",
            signal_type="BUY",
            indicator="MACD",
            price=300.0,
            message="test",
            confidence=50,
            signal_level=level,
            triggered_at=datetime.now(UTC),
        )
        db_session.add(sig)
    db_session.commit()

    resp = client.get("/api/signals/?level=STRONG")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["signal_level"] == "STRONG"


def test_get_signals_for_ticker(client, db_session):
    from datetime import UTC, datetime

    from app.models import Signal

    sig = Signal(
        ticker="NVDA",
        signal_type="SELL",
        indicator="BOLL",
        price=500.0,
        message="upper band",
        confidence=60,
        signal_level="WEAK",
        triggered_at=datetime.now(UTC),
    )
    db_session.add(sig)
    db_session.commit()

    resp = client.get("/api/signals/NVDA")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "NVDA"


def test_get_signals_for_unknown_ticker_returns_empty(client):
    resp = client.get("/api/signals/UNKN")
    assert resp.status_code == 200
    assert resp.json() == []
