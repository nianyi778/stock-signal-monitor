"""Telegram bot command and message handlers."""
import asyncio
import functools
import logging
import re

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.keyboards import (
    MAIN_KEYBOARD,
    confirm_add_inline,
    signals_inline,
    watchlist_inline,
)
from app.config import settings
from app.database import SessionLocal
from app.models import Signal, WatchlistItem

logger = logging.getLogger(__name__)


def authorized_only(func):
    """Decorator to restrict bot access to the configured chat ID."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat is None or str(update.effective_chat.id) != settings.telegram_chat_id:
            if update.message:
                await update.message.reply_text("⛔ 未授权")
            elif update.callback_query:
                await update.callback_query.answer("⛔ 未授权", show_alert=True)
            return
        return await func(update, context)
    return wrapper

# ConversationHandler state
WAITING_TICKER = 1

# 中文股票名称映射
CN_TICKER_MAP = {
    "苹果": "AAPL",
    "英伟达": "NVDA",
    "英特尔": "INTC",
    "特斯拉": "TSLA",
    "谷歌": "GOOGL",
    "微软": "MSFT",
    "亚马逊": "AMZN",
    "脸书": "META",
    "奈飞": "NFLX",
    "台积电": "TSM",
    "美光": "MU",
    "高通": "QCOM",
    "博通": "AVGO",
    "AMD": "AMD",
}


def _extract_ticker(text: str) -> str | None:
    """从自然语言提取股票代码。"""
    text = text.strip()
    # 中文映射优先
    for cn, ticker in CN_TICKER_MAP.items():
        if cn in text:
            return ticker
    # 纯字母 1-5 位 → 当作 ticker（大小写都接受，统一转大写）
    if re.fullmatch(r"[A-Za-z]{1,5}", text):
        return text.upper()
    # 文本中包含大写字母序列
    match = re.search(r"([A-Z]{1,5})", text)
    if match:
        return match.group(1)
    return None


@authorized_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *炒股达人* 已就绪\n\n"
        "每日收盘后自动扫描技术信号，多指标共振时推送分析。\n"
        "使用下方菜单操作 👇",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


@authorized_only
async def btn_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ 扫描中，请稍候...")
    from app.scheduler import scan_all_stocks
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, scan_all_stocks)
        # 查 DB 回显本次扫描结果
        db = SessionLocal()
        try:
            from datetime import datetime, timedelta, UTC
            recent = datetime.now(UTC) - timedelta(minutes=5)
            signals = (
                db.query(Signal)
                .filter(Signal.triggered_at >= recent)
                .order_by(Signal.signal_level.desc(), Signal.confidence.desc())
                .all()
            )
            if not signals:
                await update.message.reply_text("✅ 扫描完成，未检测到信号。", reply_markup=MAIN_KEYBOARD)
                return

            # Group by ticker for cleaner output
            from collections import defaultdict
            ticker_signals: dict = defaultdict(list)
            for s in signals:
                ticker_signals[s.ticker].append(s)

            # Sort tickers: STRONG first, then by max confidence
            def ticker_priority(ticker):
                sigs = ticker_signals[ticker]
                has_strong = any(s.signal_level == "STRONG" for s in sigs)
                max_conf = max(s.confidence for s in sigs)
                return (0 if has_strong else 1, -max_conf)

            sorted_tickers = sorted(ticker_signals.keys(), key=ticker_priority)

            lines = ["✅ *扫描完成*\n"]
            for ticker in sorted_tickers:
                tsigs = ticker_signals[ticker]
                # Use the most significant signal for header
                top = sorted(tsigs, key=lambda s: ({"STRONG": 0, "WEAK": 1, "WATCH": 2}[s.signal_level], -s.confidence))[0]
                level_emoji = {"STRONG": "🔴", "WEAK": "🟡", "WATCH": "⚪"}.get(top.signal_level, "⚪")
                dir_emoji = "🟢" if top.signal_type == "BUY" else "🔴" if top.signal_type == "SELL" else "🟡"
                pushed_tag = " ✈️已推送" if any(s.pushed for s in tsigs) else ""

                # Price context
                price_str = f"${top.price:.2f}" if top.price else ""
                target_str = ""
                if top.target_price and top.price:
                    pct = (top.target_price - top.price) / top.price * 100
                    arrow = "▲" if pct > 0 else "▼"
                    target_str = f" → 目标 ${top.target_price:.2f} ({arrow}{abs(pct):.1f}%)"

                # All indicators triggered
                indicators_str = " + ".join(dict.fromkeys(s.indicator for s in tsigs))

                lines.append(
                    f"{level_emoji}{dir_emoji} *{ticker}* {top.signal_level} {top.signal_type}{pushed_tag}\n"
                    f"  💰 {price_str}{target_str}\n"
                    f"  📐 指标: {indicators_str} | 置信度 {top.confidence}%\n"
                    f"  _{top.message}_"
                )

                # WEAK hint: what would make it STRONG
                if top.signal_level == "WEAK":
                    triggered = {s.indicator for s in tsigs}
                    candidates = {"MACD", "RSI", "MA_CROSS"} - triggered
                    if candidates:
                        confirm_str = " / ".join(sorted(candidates))
                        lines.append(f"  💡 _待 {confirm_str} 确认可升级 STRONG_")

            strong_count = sum(1 for s in signals if s.signal_level == "STRONG")
            weak_count = sum(1 for s in signals if s.signal_level == "WEAK")
            watch_count = sum(1 for s in signals if s.signal_level == "WATCH")
            parts = []
            if strong_count:
                parts.append(f"🔴 {strong_count} 强")
            if weak_count:
                parts.append(f"🟡 {weak_count} 弱")
            if watch_count:
                parts.append(f"⚪ {watch_count} 观察")
            summary = f"\n📊 共 {len(signals)} 条 | " + " · ".join(parts)
            if strong_count:
                summary += " | 强信号已推送 ✈️"
            else:
                summary += " | 无强信号，未推送"
            lines.append(summary)
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Scan error: {e}")
        await update.message.reply_text("❌ 扫描出错，请查看日志。", reply_markup=MAIN_KEYBOARD)


@authorized_only
async def btn_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = SessionLocal()
    try:
        signals = (
            db.query(Signal)
            .filter(Signal.signal_level == "STRONG")
            .order_by(Signal.triggered_at.desc())
            .limit(10)
            .all()
        )
        if not signals:
            await update.message.reply_text("📭 暂无强信号记录。", reply_markup=MAIN_KEYBOARD)
            return

        lines = ["📋 *最近强信号*\n"]
        tickers_seen = []
        for s in signals:
            emoji = "🟢" if s.signal_type == "BUY" else "🔴"
            lines.append(
                f"{emoji} *{s.ticker}* | {s.indicator} | 置信度 {s.confidence}%\n"
                f"  _{s.triggered_at.strftime('%m-%d %H:%M')} · {s.message}_"
            )
            if s.ticker not in tickers_seen:
                tickers_seen.append(s.ticker)

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=signals_inline(tickers_seen) if tickers_seen else MAIN_KEYBOARD,
        )
    finally:
        db.close()


@authorized_only
async def btn_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = SessionLocal()
    try:
        items = db.query(WatchlistItem).filter(WatchlistItem.is_active == True).all()  # noqa: E712
        if not items:
            await update.message.reply_text("📭 自选股为空，点击 ➕ 添加股票。", reply_markup=MAIN_KEYBOARD)
            return
        tickers = [i.ticker for i in items]
        names = {i.ticker: i.name or i.ticker for i in items}
        lines = ["📈 *我的自选股*\n"] + [f"• {t}  _{names[t]}_" for t in tickers]
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=watchlist_inline(tickers),
        )
    finally:
        db.close()


@authorized_only
async def btn_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "请输入股票名称或代码\n例如：苹果、英伟达、TSLA",
        reply_markup=MAIN_KEYBOARD,
    )
    return WAITING_TICKER


@authorized_only
async def receive_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    # 忽略菜单按钮误触发
    if text in ("📡 立即扫描", "📋 查看信号", "📈 我的自选", "➕ 添加股票"):
        return ConversationHandler.END

    ticker = _extract_ticker(text)
    if not ticker:
        await update.message.reply_text(
            "❓ 没有识别到股票代码，请重新输入（如 AAPL 或 苹果）"
        )
        return WAITING_TICKER

    # 通过 yfinance 确认公司名
    name = ticker
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get("shortName") or info.get("longName") or ticker
    except Exception:
        pass

    context.user_data["pending_ticker"] = ticker
    context.user_data["pending_name"] = name

    await update.message.reply_text(
        f"你说的是 *{ticker}* ({name}) 吗？",
        parse_mode="Markdown",
        reply_markup=confirm_add_inline(ticker),
    )
    return ConversationHandler.END


@authorized_only
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("❌ 已取消")
        return

    if data.startswith("add:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效的股票代码")
            return
        name = context.user_data.get("pending_name", ticker)
        db = SessionLocal()
        try:
            existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker).first()
            if existing and existing.is_active:
                await query.edit_message_text(f"⚠️ {ticker} 已在自选股中")
                return
            if existing:
                existing.is_active = True
                db.commit()
            else:
                db.add(WatchlistItem(ticker=ticker, name=name))
                db.commit()
            await query.edit_message_text(f"✅ *{ticker}* ({name}) 已加入自选股", parse_mode="Markdown")
        finally:
            db.close()

    elif data.startswith("del:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效的股票代码")
            return
        db = SessionLocal()
        try:
            item = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker).first()
            if item:
                item.is_active = False
                db.commit()
            await query.edit_message_text(f"🗑 *{ticker}* 已从自选股移除", parse_mode="Markdown")
        finally:
            db.close()

    elif data.startswith("sig:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效的股票代码")
            return
        db = SessionLocal()
        try:
            signals = (
                db.query(Signal)
                .filter(Signal.ticker == ticker)
                .order_by(Signal.triggered_at.desc())
                .limit(5)
                .all()
            )
            if not signals:
                await query.edit_message_text(f"📭 {ticker} 暂无信号记录")
                return
            lines = [f"📊 *{ticker} 信号历史*\n"]
            for s in signals:
                emoji = "🟢" if s.signal_type == "BUY" else ("🔴" if s.signal_type == "SELL" else "🟡")
                lines.append(
                    f"{emoji} {s.signal_level} {s.signal_type} | {s.indicator} | {s.confidence}%\n"
                    f"  _{s.triggered_at.strftime('%m-%d %H:%M')} · {s.message}_"
                )
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        finally:
            db.close()

    elif data.startswith("analyze:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效的股票代码")
            return
        await query.edit_message_text(f"⏳ 正在分析 {ticker}...")
        from app.bot.analysis import get_stock_analysis
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, get_stock_analysis, ticker)
        # Telegram message max 4096 chars
        if len(result) > 4096:
            result = result[:4090] + "\n..."
        await query.message.reply_text(result, parse_mode="Markdown")


@authorized_only
async def btn_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.calendar import get_upcoming_events_from_db
    result = get_upcoming_events_from_db(days=14)
    if len(result) > 4096:
        result = result[:4090] + "\n..."
    await update.message.reply_text(result, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


def build_handlers() -> list:
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ 添加股票$"), btn_add_start)],
        states={WAITING_TICKER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ticker)]},
        fallbacks=[CommandHandler("cancel", cmd_start)],
    )
    return [
        CommandHandler("start", cmd_start),
        MessageHandler(filters.Regex("^📡 立即扫描$"), btn_scan),
        MessageHandler(filters.Regex("^📋 查看信号$"), btn_signals),
        MessageHandler(filters.Regex("^📈 我的自选$"), btn_watchlist),
        MessageHandler(filters.Regex("^📅 大事日历$"), btn_calendar),
        CallbackQueryHandler(callback_handler),
        conv,
    ]
