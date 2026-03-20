import asyncio
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import WatchlistItem, Signal
from app.schemas import SignalCreate
from app.signals.engine import run_signals, SignalResult
from app.config import settings

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler()


def scan_all_stocks() -> None:
    """
    Main daily scan job:
    1. Get active watchlist items
    2. Run signals for each ticker
    3. Save ALL signals to DB (including WEAK and WATCH for history)
    4. Push only STRONG signals with confidence >= push_min_confidence via Telegram + LLM
    """
    db = SessionLocal()
    try:
        tickers = [item.ticker for item in db.query(WatchlistItem).filter(WatchlistItem.is_active == True).all()]
        logger.info(f"Starting scan for {len(tickers)} tickers")

        for ticker in tickers:
            try:
                signals = run_signals(ticker)
                if not signals:
                    logger.info(f"No signals for {ticker}")
                    continue

                # Save ALL signals to DB
                new_db_signals = []
                for sig in signals:
                    db_signal = Signal(
                        ticker=sig.ticker,
                        signal_type=sig.signal_type,
                        indicator=sig.indicator,
                        price=sig.price,
                        target_price=sig.target_price,
                        message=sig.message,
                        confidence=sig.confidence,
                        signal_level=sig.signal_level,
                        pushed=False,
                    )
                    db.add(db_signal)
                    new_db_signals.append(db_signal)
                db.flush()  # get IDs before commit
                new_signal_ids = [s.id for s in new_db_signals]
                db.commit()

                # Filter for push: STRONG + confidence >= threshold
                push_signals = [
                    s for s in signals
                    if s.signal_level == "STRONG" and s.confidence >= settings.push_min_confidence
                ]

                if not push_signals:
                    logger.info(f"No push-worthy signals for {ticker}")
                    continue

                # Get price context for LLM
                import yfinance as yf
                hist = yf.download(ticker, period="5d", progress=False)
                price_context = {
                    "current_price": push_signals[0].price,
                    "5d_change_pct": 0.0,
                    "support": None,
                    "resistance": None,
                }
                if hist is not None and len(hist) >= 2:
                    start_price = float(hist["Close"].iloc[0])
                    end_price = float(hist["Close"].iloc[-1])
                    price_context["5d_change_pct"] = round((end_price - start_price) / start_price * 100, 2)

                # LLM summary + Telegram push (async in sync context)
                from app.llm.summarizer import summarize_signals
                from app.notifications.telegram import format_signal_message, send_telegram

                summary = asyncio.run(summarize_signals(ticker, push_signals, price_context))
                message = format_signal_message(ticker, push_signals, summary)
                success = asyncio.run(send_telegram(message))

                if success:
                    # Mark only the newly inserted signals as pushed
                    pushed_ids = [
                        s.id for s in new_db_signals
                        if s.signal_level == "STRONG" and s.confidence >= settings.push_min_confidence
                    ]
                    if pushed_ids:
                        db.query(Signal).filter(
                            Signal.id.in_(pushed_ids)
                        ).update({"pushed": True}, synchronize_session=False)
                        db.commit()
                    logger.info(f"Pushed signal for {ticker}")
                else:
                    logger.warning(f"Failed to push Telegram message for {ticker}")

            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}", exc_info=True)
                db.rollback()

    finally:
        db.close()


def scan_all_stocks_sync() -> None:
    """Synchronous wrapper for use in FastAPI BackgroundTasks."""
    scan_all_stocks()


def start_scheduler() -> None:
    """Start APScheduler with daily cron job at configured hour."""
    _scheduler.add_job(
        scan_all_stocks,
        CronTrigger(hour=settings.scheduler_cron_hour, minute=0, timezone="America/New_York"),
        id="daily_scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"Scheduler started. Daily scan at {settings.scheduler_cron_hour}:00 ET")


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
