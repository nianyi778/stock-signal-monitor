"""Tests for app/scheduler.py — scan_all_stocks()."""

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import WatchlistItem, Signal
from app.signals.engine import SignalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def _make_signal(ticker, signal_type="BUY", signal_level="STRONG", confidence=80):
    return SignalResult(
        ticker=ticker,
        signal_type=signal_type,
        indicator="MACD",
        price=100.0,
        target_price=110.0,
        confidence=confidence,
        signal_level=signal_level,
        message="test signal",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanAllStocks:

    def test_scan_no_active_stocks(self):
        """Empty watchlist → run_signals never called, no signals in DB."""
        session = _make_session()
        mock_settings = MagicMock()
        mock_settings.push_min_confidence = 60

        with patch("app.scheduler.SessionLocal", return_value=session), \
             patch("app.scheduler.run_signals") as mock_run, \
             patch("app.scheduler.settings", mock_settings):

            from app.scheduler import scan_all_stocks
            scan_all_stocks()

            mock_run.assert_not_called()
            assert session.query(Signal).count() == 0

        session.close()

    def test_scan_saves_all_signals_to_db(self):
        """Two signals (STRONG + WEAK) are both persisted to the database."""
        session = _make_session()
        session.add(WatchlistItem(ticker="AAPL", is_active=True))
        session.commit()

        strong_sig = _make_signal("AAPL", "BUY", "STRONG", 85)
        weak_sig = _make_signal("AAPL", "BUY", "WEAK", 45)

        mock_settings = MagicMock()
        mock_settings.push_min_confidence = 60

        with patch("app.scheduler.SessionLocal", return_value=session), \
             patch("app.scheduler.run_signals", return_value=[strong_sig, weak_sig]), \
             patch("app.scheduler.settings", mock_settings), \
             patch("yfinance.download", return_value=None), \
             patch("app.llm.summarizer.summarize_signals") as mock_summarize, \
             patch("app.notifications.telegram.send_telegram") as mock_tg:

            mock_summarize.return_value = "mock summary"
            mock_tg.return_value = True

            # Patch asyncio.run to call coroutines synchronously
            import asyncio

            def fake_asyncio_run(coro):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            with patch("app.scheduler.asyncio.run", side_effect=fake_asyncio_run):
                from app.scheduler import scan_all_stocks
                scan_all_stocks()

            saved = session.query(Signal).all()
            assert len(saved) == 2

        session.close()

    def test_scan_only_pushes_strong_signals(self):
        """STRONG signal → pushed=True; WEAK signal → pushed=False."""
        session = _make_session()
        session.add(WatchlistItem(ticker="AAPL", is_active=True))
        session.commit()

        strong_sig = _make_signal("AAPL", "BUY", "STRONG", 80)
        weak_sig = _make_signal("AAPL", "BUY", "WEAK", 50)

        mock_settings = MagicMock()
        mock_settings.push_min_confidence = 60

        async def mock_summarize(*args, **kwargs):
            return "mock summary"

        async def mock_send(*args, **kwargs):
            return True

        with patch("app.scheduler.SessionLocal", return_value=session), \
             patch("app.scheduler.run_signals", return_value=[strong_sig, weak_sig]), \
             patch("app.scheduler.settings", mock_settings), \
             patch("yfinance.download", return_value=None), \
             patch("app.llm.summarizer.summarize_signals", mock_summarize), \
             patch("app.notifications.telegram.send_telegram", mock_send), \
             patch("app.notifications.telegram.format_signal_message", return_value="msg"):

            import asyncio

            def fake_asyncio_run(coro):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            with patch("app.scheduler.asyncio.run", side_effect=fake_asyncio_run):
                from app.scheduler import scan_all_stocks
                scan_all_stocks()

            signals_in_db = session.query(Signal).all()
            strong_in_db = [s for s in signals_in_db if s.signal_level == "STRONG"]
            weak_in_db = [s for s in signals_in_db if s.signal_level == "WEAK"]

            assert len(strong_in_db) == 1
            assert strong_in_db[0].pushed is True
            assert len(weak_in_db) == 1
            assert weak_in_db[0].pushed is False

        session.close()

    def test_scan_skips_push_below_confidence(self):
        """STRONG signal with confidence 40 (below threshold 60) → pushed=False."""
        session = _make_session()
        session.add(WatchlistItem(ticker="TSLA", is_active=True))
        session.commit()

        low_conf_sig = _make_signal("TSLA", "BUY", "STRONG", 40)

        mock_settings = MagicMock()
        mock_settings.push_min_confidence = 60

        with patch("app.scheduler.SessionLocal", return_value=session), \
             patch("app.scheduler.run_signals", return_value=[low_conf_sig]), \
             patch("app.scheduler.settings", mock_settings):

            from app.scheduler import scan_all_stocks
            scan_all_stocks()

            signals_in_db = session.query(Signal).all()
            assert len(signals_in_db) == 1
            assert signals_in_db[0].pushed is False

        session.close()

    def test_scan_handles_run_signals_error(self):
        """run_signals raises exception for one ticker → other tickers still processed."""
        session = _make_session()
        session.add(WatchlistItem(ticker="BOOM", is_active=True))
        session.add(WatchlistItem(ticker="TSLA", is_active=True))
        session.commit()

        good_sig = _make_signal("TSLA", "BUY", "STRONG", 40)
        mock_settings = MagicMock()
        mock_settings.push_min_confidence = 60

        call_log = []

        def side_effect(ticker):
            call_log.append(ticker)
            if ticker == "BOOM":
                raise RuntimeError("data unavailable")
            return [good_sig]

        with patch("app.scheduler.SessionLocal", return_value=session), \
             patch("app.scheduler.run_signals", side_effect=side_effect), \
             patch("app.scheduler.settings", mock_settings):

            from app.scheduler import scan_all_stocks
            # Should not raise
            scan_all_stocks()

            # Both tickers were attempted
            assert "BOOM" in call_log
            assert "TSLA" in call_log
            # TSLA signal was saved (confidence below push threshold, so no Telegram)
            saved = session.query(Signal).filter(Signal.ticker == "TSLA").all()
            assert len(saved) == 1

        session.close()


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


def test_daily_job_calls_outcome_evaluation():
    """_daily_job() invokes evaluate_signal_outcomes after check_active_trades."""
    from unittest.mock import patch, MagicMock

    mock_db = MagicMock()

    # SessionLocal() returns mock_db (direct call, not context manager)
    with patch("app.scheduler.scan_all_stocks") as mock_scan, \
         patch("app.scheduler.check_active_trades") as mock_check, \
         patch("app.scheduler.SessionLocal", return_value=mock_db), \
         patch("app.learning.outcome_tracker.evaluate_signal_outcomes", return_value=3) as mock_eval:
        import app.scheduler as sched_module
        sched_module._daily_job()

    mock_scan.assert_called_once()
    mock_check.assert_called_once()
    mock_eval.assert_called_once_with(mock_db)
    mock_db.close.assert_called_once()
