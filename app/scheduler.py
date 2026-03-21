import asyncio
import logging
import concurrent.futures
from datetime import UTC, datetime
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


def _run_async(coro):
    """Run a coroutine from a sync context (thread pool or scheduler thread).

    APScheduler and run_in_executor both run in threads without an active event loop.
    asyncio.run() creates a fresh loop, which is safe here.
    FastAPI BackgroundTasks can run in the event loop thread — detect that case.
    """
    try:
        loop = asyncio.get_running_loop()
        # We're inside the event loop thread — schedule the coroutine safely
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=60)
    except RuntimeError:
        # No running loop in this thread — safe to use asyncio.run()
        return asyncio.run(coro)


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

        # ── Phase B: Market Sentiment (once per scan run) ─────────────────────
        from app.data.market_sentiment import get_market_sentiment
        market_sentiment = _run_async(get_market_sentiment(tickers))
        logger.info(
            f"Market sentiment: F&G={market_sentiment.fear_greed_score} "
            f"({market_sentiment.fear_greed_label}), "
            f"VIX slope={market_sentiment.vix_30d_slope:+.3f}, "
            f"composite={market_sentiment.composite_score}"
        )

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

                # Get price context for LLM / debate
                import yfinance as yf
                import pandas as pd
                hist = yf.download(ticker, period="5d", progress=False)
                if hist is not None and isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.droplevel(1)
                sig0 = push_signals[0]
                price_context = {
                    "current_price": sig0.price,
                    "5d_change_pct": 0.0,
                    "support": sig0.stop_price,       # ATR stop = effective support floor
                    "resistance": sig0.target_price,  # target = nearest resistance
                }
                if hist is not None and len(hist) >= 2:
                    start_price = float(hist["Close"].iloc[0])
                    end_price = float(hist["Close"].iloc[-1])
                    price_context["5d_change_pct"] = round((end_price - start_price) / start_price * 100, 2)

                # ── Step 1: News sentiment ────────────────────────────────────────
                from app.data.news import get_ticker_sentiment, apply_sentiment_to_confidence
                sentiment = _run_async(get_ticker_sentiment(ticker))
                if sentiment:
                    logger.info(
                        f"{ticker}: news sentiment bullish={sentiment['bullish_pct']:.0%} "
                        f"bearish={sentiment['bearish_pct']:.0%}"
                    )
                    # Adjust push_signals confidence based on news sentiment
                    for sig in push_signals:
                        sig.confidence = apply_sentiment_to_confidence(sig.confidence, sentiment)

                # ── Step 2: Bull/Bear debate (if enabled) ─────────────────────────
                debate_result = None
                if settings.enable_debate:
                    from app.llm.debate import debate_signal
                    debate_result = _run_async(debate_signal(ticker, push_signals, price_context, sentiment))
                    logger.info(f"{ticker}: debate → {debate_result.decision} — {debate_result.verdict}")
                    if debate_result.decision in ("SUPPRESS", "DOWNGRADE"):
                        # Downgrade the DB records so they don't appear as un-pushed STRONG signals
                        db.query(Signal).filter(
                            Signal.id.in_(new_signal_ids)
                        ).update({"signal_level": "WEAK"}, synchronize_session=False)
                        db.commit()
                        logger.info(f"{ticker}: signal {debate_result.decision.lower()}d by debate")
                        continue

                # ── Phase B: Apply market-wide sentiment adjustments ──────────────
                fg = market_sentiment.fear_greed_score
                vix_slope = market_sentiment.vix_30d_slope

                for sig in push_signals:
                    # Contrarian: extreme fear is good for BUY, bad for SELL
                    if fg < 25 and sig.signal_type == "BUY":
                        sig.confidence = min(95, sig.confidence + 5)
                    elif fg < 25 and sig.signal_type == "SELL":
                        sig.confidence = max(0, sig.confidence - 10)
                    elif fg > 75 and sig.signal_type == "BUY":
                        sig.confidence = max(0, sig.confidence - 5)
                    elif fg > 75 and sig.signal_type == "SELL":
                        sig.confidence = min(95, sig.confidence + 5)

                # Adjust effective push threshold based on VIX trend (in-memory only).
                # Use get_param() so Phase C auto-tuned values are respected.
                from app.learning.params import get_param
                effective_min_confidence = int(get_param(db, "push_min_confidence", settings.push_min_confidence))
                if vix_slope > 0.3:
                    effective_min_confidence += 5   # fear rising → be stricter
                elif vix_slope < -0.3:
                    effective_min_confidence = max(45, effective_min_confidence - 3)

                push_signals = [
                    s for s in push_signals
                    if s.confidence >= effective_min_confidence
                ]
                if not push_signals:
                    logger.info(f"No push-worthy signals for {ticker} after sentiment filter")
                    continue

                # ── Step 3: Position-aware note ────────────────────────────────────
                from app.models import PositionEntry
                position_note = None
                pos_entries = db.query(PositionEntry).filter_by(ticker=ticker, is_active=True).all()
                if pos_entries:
                    total_shares = sum(e.shares for e in pos_entries)
                    avg_price = sum(e.buy_price * e.shares for e in pos_entries) / total_shares
                    curr = sig0.price
                    pnl_pct = (curr - avg_price) / avg_price * 100
                    if pnl_pct < -10:
                        position_note = (
                            f"⚠️ 已持 {total_shares:.0f}股 @ ${avg_price:.2f}，"
                            f"浮亏 {abs(pnl_pct):.1f}%，请谨慎加仓"
                        )
                    elif pnl_pct >= 0:
                        position_note = (
                            f"📦 已持 {total_shares:.0f}股 @ ${avg_price:.2f}（+{pnl_pct:.1f}%）"
                        )
                    else:
                        position_note = (
                            f"📦 已持 {total_shares:.0f}股 @ ${avg_price:.2f}（{pnl_pct:.1f}%）"
                        )

                # ── LLM summary + Telegram push ────────────────────────────────────
                from app.llm.summarizer import summarize_signals
                from app.notifications.telegram import format_signal_message, send_telegram

                verdict_str = debate_result.verdict if debate_result else None
                summary = _run_async(summarize_signals(ticker, push_signals, price_context))
                message = format_signal_message(
                    ticker, push_signals, summary,
                    verdict=verdict_str,
                    position_note=position_note,
                )
                success = _run_async(send_telegram(message))

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

                    # Create ActiveTrade records for pushed STRONG signals with stop_price
                    from app.models import ActiveTrade
                    from datetime import timedelta
                    try:
                        earnings_dt = None
                        cal = yf.Ticker(ticker).calendar
                        if cal is not None and len(cal) > 0:
                            dates = cal.get("Earnings Date", [])
                            if dates:
                                from datetime import date, UTC as _UTC
                                d = dates[0]
                                if hasattr(d, 'year'):
                                    earnings_dt = datetime(d.year, d.month, d.day, tzinfo=_UTC)
                    except Exception:
                        earnings_dt = None

                    # Find the DB signal record for each push signal (match by index)
                    pushed_db_signals = [
                        s for s in new_db_signals
                        if s.signal_level == "STRONG" and s.confidence >= settings.push_min_confidence
                    ]
                    for sig, db_signal in zip(push_signals, pushed_db_signals):
                        existing = db.query(ActiveTrade).filter(
                            ActiveTrade.ticker == ticker,
                            ActiveTrade.status == "ACTIVE",
                        ).first()
                        if existing:
                            continue
                        if sig.stop_price:
                            trade = ActiveTrade(
                                ticker=ticker,
                                signal_id=db_signal.id,
                                entry_low=sig.entry_low,
                                entry_high=sig.entry_high,
                                target_price=sig.target_price,
                                stop_price=sig.stop_price,
                                warn_price=sig.warn_price,
                                partial_tp=sig.partial_tp,
                                rr_ratio=sig.rr_ratio,
                                atr_at_signal=sig.atr,
                                volume_ratio=sig.volume_ratio,
                                regime_state=sig.regime,
                                earnings_date=earnings_dt,
                                status="ACTIVE",
                                valid_until=datetime.now(UTC) + timedelta(days=3),
                            )
                            db.add(trade)
                    db.commit()
                    logger.info(f"Pushed signal for {ticker}")
                else:
                    logger.warning(f"Failed to push Telegram message for {ticker}")

            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}", exc_info=True)
                db.rollback()

    finally:
        db.close()


def check_active_trades(db=None, price_override: dict | None = None):
    """Daily check of all ACTIVE trades. Push Telegram on triggers."""
    import yfinance as yf
    from datetime import UTC, datetime
    from app.models import ActiveTrade
    from app.notifications.telegram import send_telegram

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now = datetime.now(UTC)
        trades = db.query(ActiveTrade).filter(ActiveTrade.status == "ACTIVE").all()

        for trade in trades:
            ticker = trade.ticker

            # Get current price
            if price_override and ticker in price_override:
                price = price_override[ticker]
            else:
                try:
                    price = float(getattr(yf.Ticker(ticker).fast_info, 'last_price', None) or 0)
                except Exception:
                    continue
            if price <= 0:
                continue

            # Expiry check (before price checks)
            if trade.valid_until:
                valid_until = trade.valid_until
                if valid_until.tzinfo is None:
                    valid_until = valid_until.replace(tzinfo=UTC)
                if now > valid_until:
                    trade.status = "EXPIRED"
                    trade.closed_at = now
                    db.commit()
                    continue

            # Stop loss
            if trade.stop_price and price <= trade.stop_price:
                trade.status = "STOPPED"
                trade.closed_at = now
                db.commit()
                msg = (
                    f"🔴 *止损提醒* — {ticker}\n"
                    f"  当前价 ${price:.2f} ≤ 止损价 ${trade.stop_price:.2f}\n"
                    f"  建议立即止损，控制损失。"
                )
                _run_async(send_telegram(msg))
                continue

            # Target hit
            if trade.target_price and price >= trade.target_price:
                trade.status = "TARGET_HIT"
                trade.closed_at = now
                db.commit()
                msg = (
                    f"✅ *目标达成* — {ticker}\n"
                    f"  当前价 ${price:.2f} ≥ 目标价 ${trade.target_price:.2f}\n"
                    f"  恭喜！建议考虑止盈。"
                )
                _run_async(send_telegram(msg))
                continue

            # Partial TP
            if trade.partial_tp and price >= trade.partial_tp:
                mid_entry = (trade.entry_low + trade.entry_high) / 2 if trade.entry_low and trade.entry_high else price
                msg = (
                    f"💰 *分批止盈区间* — {ticker}\n"
                    f"  当前价 ${price:.2f}（目标 95% = ${trade.partial_tp:.2f}）\n"
                    f"  建议卖出 50%，止损上移至保本价 ${mid_entry:.2f}"
                )
                _run_async(send_telegram(msg))

            # Warning
            elif trade.warn_price and price <= trade.warn_price:
                msg = (
                    f"⚠️ *预警* — {ticker}\n"
                    f"  当前价 ${price:.2f} 接近止损位 ${trade.stop_price:.2f}\n"
                    f"  建议密切关注。"
                )
                _run_async(send_telegram(msg))

            # Earnings warning
            if trade.earnings_date:
                earnings_date = trade.earnings_date
                if hasattr(earnings_date, 'date'):
                    earnings_day = earnings_date.date()
                else:
                    earnings_day = earnings_date
                days_to_earnings = (earnings_day - now.date()).days
                if days_to_earnings == 7:
                    _run_async(send_telegram(
                        f"📅 *财报预警（7天）* — {ticker}\n"
                        f"  财报日: {earnings_day}，建议缩减至 50% 仓位。"
                    ))
                elif days_to_earnings == 2:
                    _run_async(send_telegram(
                        f"🔴 *财报临近（2天）* — {ticker}\n"
                        f"  建议清仓规避缺口风险！财报日: {earnings_day}"
                    ))
    finally:
        if close_db:
            db.close()


def scan_all_stocks_sync() -> None:
    """Synchronous wrapper for use in FastAPI BackgroundTasks."""
    scan_all_stocks()


def refresh_calendar_job() -> None:
    """Daily job to refresh economic calendar from all sources."""
    from app.bot.calendar import refresh_calendar
    try:
        result = refresh_calendar()
        logger.info(f"Calendar refresh: {result}")
    except Exception as e:
        logger.error(f"Calendar refresh error: {e}", exc_info=True)


def _daily_job():
    """Wrapper that runs scan, position monitor, and signal outcome evaluation."""
    scan_all_stocks()
    check_active_trades()
    db = SessionLocal()
    try:
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)
        if count:
            logger.info(f"Evaluated {count} signal outcomes")
    except Exception as e:
        logger.error(f"Signal outcome evaluation error: {e}", exc_info=True)
    finally:
        db.close()


def start_scheduler() -> None:
    """Start APScheduler with daily cron jobs."""
    _scheduler.add_job(
        _daily_job,
        CronTrigger(hour=settings.scheduler_cron_hour, minute=0, timezone="America/New_York"),
        id="daily_scan",
        replace_existing=True,
    )
    # Calendar refresh: twice daily (8:00 and 20:00 ET)
    _scheduler.add_job(
        refresh_calendar_job,
        CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        id="calendar_refresh_am",
        replace_existing=True,
    )
    _scheduler.add_job(
        refresh_calendar_job,
        CronTrigger(hour=20, minute=0, timezone="America/New_York"),
        id="calendar_refresh_pm",
        replace_existing=True,
    )
    # Also refresh on startup
    refresh_calendar_job()
    _scheduler.start()
    logger.info(f"Scheduler started. Scan at {settings.scheduler_cron_hour}:00 ET, calendar at 8:00/20:00 ET")


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
