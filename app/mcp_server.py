"""
Stock Signal Monitor — MCP Server (fastmcp)

Exposes stock monitoring capabilities as MCP tools.
Supports two transport modes:
  - stdio: for Claude Desktop / Claude Code (local)
  - http:  for remote/Docker deployment (port 8001)

Usage:
    python -m app.mcp_server            # stdio (default)
    python -m app.mcp_server --http     # HTTP on port 8001

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "stock_monitor": {
          "command": "python",
          "args": ["-m", "app.mcp_server"],
          "cwd": "/path/to/stock-signal-monitor",
          "env": {
            "DATABASE_URL": "...",
            "TELEGRAM_BOT_TOKEN": "...",
            "TELEGRAM_CHAT_ID": "...",
            "OPENAI_API_KEY": "...",
            "FINNHUB_API_KEY": "..."
          }
        }
      }
    }

Remote (HTTP) — Claude Code settings.json:
    {
      "mcpServers": {
        "stock_monitor": {
          "type": "http",
          "url": "http://your-server:8001/mcp"
        }
      }
    }
"""

import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="Stock Signal Monitor",
    instructions=(
        "Access a stock signal monitoring system. "
        "You can manage a watchlist, trigger technical signal scans (MACD/RSI/MA/Bollinger), "
        "get full stock analysis with entry range / target / stop loss prices and risk:reward ratio, "
        "track a portfolio with buy entries and real-time P&L, monitor active trades for stop/target triggers, "
        "and view the US economic event calendar with market forecasts."
    ),
)


# ── Input models ─────────────────────────────────────────────────────────────

class TickerInput(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol, e.g. AAPL, NVDA, TSLA", min_length=1, max_length=10)


class SignalsInput(BaseModel):
    ticker: Optional[str] = Field(None, description="Filter by ticker. Omit for all.")
    level: Optional[str] = Field(None, description="STRONG, WEAK, or WATCH")
    limit: int = Field(20, ge=1, le=100)


class CalendarInput(BaseModel):
    days: int = Field(14, description="Days ahead to look", ge=1, le=90)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db():
    from app.database import SessionLocal
    return SessionLocal()


def _level_emoji(level: str) -> str:
    return {"STRONG": "🔴", "WEAK": "🟡", "WATCH": "⚪"}.get(level, "⚪")


def _dir_emoji(t: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "WATCH": "🟡"}.get(t, "⚪")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool
def stock_monitor_get_watchlist() -> str:
    """List all active stocks in the watchlist being monitored for signals."""
    from app.models import WatchlistItem
    db = _db()
    try:
        items = db.query(WatchlistItem).filter(WatchlistItem.is_active == True).all()  # noqa: E712
        if not items:
            return "Watchlist is empty."
        lines = [f"📈 Watchlist ({len(items)} stocks):\n"]
        for item in items:
            name_str = f" — {item.name}" if item.name else ""
            lines.append(f"  • {item.ticker}{name_str}")
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool
def stock_monitor_add_stock(ticker: str) -> str:
    """
    Add a stock ticker to the watchlist.
    Automatically fetches company name. Will be included in the next scan.

    Args:
        ticker: Stock ticker symbol, e.g. AAPL
    """
    import re
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z]{1,10}", ticker):
        return f"❌ Invalid ticker: {ticker}"

    from app.models import WatchlistItem
    db = _db()
    try:
        existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker).first()
        if existing and existing.is_active:
            return f"⚠️ {ticker} is already in the watchlist."
        name = ticker
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            name = info.get("shortName") or info.get("longName") or ticker
        except Exception:
            pass
        if existing:
            existing.is_active = True
            db.commit()
        else:
            db.add(WatchlistItem(ticker=ticker, name=name))
            db.commit()
        return f"✅ Added {ticker} ({name}) to watchlist."
    finally:
        db.close()


@mcp.tool
def stock_monitor_remove_stock(ticker: str) -> str:
    """
    Remove a stock ticker from the watchlist (soft delete, keeps signal history).

    Args:
        ticker: Stock ticker symbol to remove
    """
    from app.models import WatchlistItem
    ticker = ticker.upper().strip()
    db = _db()
    try:
        item = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker).first()
        if not item or not item.is_active:
            return f"⚠️ {ticker} is not in the active watchlist."
        item.is_active = False
        db.commit()
        return f"🗑 Removed {ticker} from watchlist."
    finally:
        db.close()


@mcp.tool
def stock_monitor_get_signals(
    ticker: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    Get recent trading signals. Levels: STRONG (2+ indicators confluent),
    WEAK (single indicator), WATCH (near threshold).

    Args:
        ticker: Filter by ticker symbol (optional)
        level: Filter by signal level: STRONG, WEAK, or WATCH (optional)
        limit: Max results to return (default 20, max 100)
    """
    from app.models import Signal
    db = _db()
    try:
        query = db.query(Signal).order_by(Signal.triggered_at.desc())
        if ticker:
            query = query.filter(Signal.ticker == ticker.upper())
        if level:
            query = query.filter(Signal.signal_level == level.upper())
        signals = query.limit(min(limit, 100)).all()

        if not signals:
            return "No signals found."

        lines = [f"📊 {len(signals)} signal(s):\n"]
        for s in signals:
            pushed = " ✈️" if s.pushed else ""
            lines.append(
                f"{_level_emoji(s.signal_level)}{_dir_emoji(s.signal_type)} "
                f"{s.ticker} [{s.signal_level}] {s.signal_type} | "
                f"{s.indicator} | {s.confidence}%{pushed} | "
                f"{s.triggered_at.strftime('%m-%d %H:%M')}\n"
                f"   {s.message}"
            )
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool
def stock_monitor_scan() -> str:
    """
    Trigger an immediate scan of all watchlist stocks.
    Runs MACD, RSI, MA cross, Bollinger confluence detection.
    STRONG signals are sent to Telegram automatically.
    Takes 10–30s depending on watchlist size.
    """
    import asyncio
    from app.scheduler import scan_all_stocks

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.get_event_loop().run_in_executor(None, scan_all_stocks))
        loop.close()
    except Exception:
        # Fallback: run directly
        scan_all_stocks()

    from app.models import Signal
    db = _db()
    try:
        recent = datetime.now(UTC) - timedelta(minutes=5)
        signals = (
            db.query(Signal)
            .filter(Signal.triggered_at >= recent)
            .order_by(Signal.signal_level.desc(), Signal.confidence.desc())
            .all()
        )
        if not signals:
            return "✅ Scan complete. No signals detected."

        lines = [f"✅ Scan complete — {len(signals)} signal(s):\n"]
        for s in signals:
            pushed_tag = " ✈️ pushed" if s.pushed else ""
            lines.append(
                f"{_level_emoji(s.signal_level)}{_dir_emoji(s.signal_type)} "
                f"{s.ticker} [{s.signal_level}] {s.signal_type} | "
                f"{s.indicator} | {s.confidence}%{pushed_tag}\n"
                f"   {s.message}"
            )
        strong = sum(1 for s in signals if s.signal_level == "STRONG")
        lines.append(
            f"\n{'⚡ ' + str(strong) + ' STRONG signal(s) pushed to Telegram.' if strong else 'No STRONG signals — Telegram not notified.'}"
        )
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool
def stock_monitor_analyze(ticker: str) -> str:
    """
    Full stock analysis: current price, pre/post-market, support/resistance,
    action recommendation (buy/sell/hold with price ranges and stop loss),
    RSI, MA trend, analyst consensus, short interest, beta,
    and upcoming earnings + macro events (FOMC/CPI/NFP).

    Args:
        ticker: Stock ticker symbol, e.g. NVDA
    """
    from app.bot.analysis import get_stock_analysis
    return get_stock_analysis(ticker.upper().strip())


@mcp.tool
def stock_monitor_get_calendar(days: int = 14) -> str:
    """
    Upcoming US economic events: FOMC, CPI, NFP, PCE, GDP and watchlist earnings.
    Includes market consensus forecast and prior period values when available.

    Args:
        days: Number of days ahead to show (default 14, max 90)
    """
    from app.bot.calendar import get_upcoming_events_from_db
    return get_upcoming_events_from_db(days=min(days, 90))


@mcp.tool
def stock_monitor_refresh_calendar() -> str:
    """
    Force-refresh economic calendar from Finnhub.
    Updates earnings estimates and macro event forecast/prior values.
    """
    from app.bot.calendar import refresh_calendar
    try:
        result = refresh_calendar()
        return (
            f"✅ Calendar refreshed:\n"
            f"  • {result.get('official', 0)} official macro events synced\n"
            f"  • {result.get('macro_enriched', 0)} events enriched with forecast/prior\n"
            f"  • {result.get('earnings', 0)} earnings events updated"
        )
    except Exception as e:
        return f"❌ Refresh failed: {e}"


@mcp.tool
def stock_monitor_get_active_trades(status: Optional[str] = None) -> str:
    """
    List active trade monitoring records created from STRONG signals.
    Shows entry range, target, stop loss, warn level, R:R, and current status.

    Args:
        status: Filter by status — ACTIVE, STOPPED, TARGET_HIT, EXPIRED, CANCELLED.
                Omit to show all ACTIVE trades.
    """
    from app.models import ActiveTrade
    db = _db()
    try:
        filter_status = status.upper() if status else "ACTIVE"
        trades = (
            db.query(ActiveTrade)
            .filter(ActiveTrade.status == filter_status)
            .order_by(ActiveTrade.opened_at.desc())
            .limit(50)
            .all()
        )
        if not trades:
            return f"No {filter_status} trades found."

        lines = [f"📊 {len(trades)} {filter_status} trade(s):\n"]
        for t in trades:
            rr = f"  R:R {t.rr_ratio:.1f}" if t.rr_ratio else ""
            entry = f"${t.entry_low:.2f}~${t.entry_high:.2f}" if t.entry_low and t.entry_high else "—"
            lines.append(
                f"{'🟢' if t.status == 'ACTIVE' else '⚪'} {t.ticker} | "
                f"进场 {entry} | 目标 ${t.target_price:.2f} | 止损 ${t.stop_price:.2f}{rr}\n"
                f"   状态: {t.status} | 有效至: {t.valid_until.strftime('%m-%d') if t.valid_until else '—'} | "
                f"开仓: {t.opened_at.strftime('%m-%d %H:%M') if t.opened_at else '—'}"
            )
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool
def stock_monitor_add_position(ticker: str, buy_price: float, shares: float, note: str = "") -> str:
    """
    Record a stock buy entry for portfolio tracking.
    Multiple entries per ticker are supported (different lots at different prices).
    Weighted average cost basis is calculated automatically.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA
        buy_price: Purchase price per share, e.g. 882.5
        shares: Number of shares purchased, e.g. 20
        note: Optional note, e.g. "lot 2" or "averaging down"
    """
    import re
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z]{1,10}", ticker):
        return f"❌ Invalid ticker: {ticker}"
    if buy_price <= 0 or shares <= 0:
        return "❌ buy_price and shares must be greater than zero."

    from app.bot.portfolio import add_position
    db = _db()
    try:
        entry = add_position(db, ticker, buy_price, shares, note)
        return (
            f"✅ Recorded: {ticker}  {shares:.0f} shares @ ${buy_price:.2f}\n"
            f"   Entry ID: {entry.id}"
        )
    except Exception as e:
        return f"❌ Failed to record position: {e}"
    finally:
        db.close()


@mcp.tool
def stock_monitor_get_positions() -> str:
    """
    Get all active portfolio positions with real-time P&L.
    Shows weighted average cost, current price, unrealized gain/loss per position.
    Requires PORTFOLIO_VALUE in .env for position sizing percentages.
    """
    from app.bot.portfolio import get_all_positions, format_portfolio_message
    from app.config import settings
    db = _db()
    try:
        positions = get_all_positions(db)
        if not positions:
            return "📭 No active positions recorded."
        portfolio_value = float(getattr(settings, "portfolio_value", 0) or 0)
        if portfolio_value > 0:
            for p in positions:
                curr = p.get("current_price") or 0
                p["position_pct"] = curr * p["total_shares"] / portfolio_value * 100
        return format_portfolio_message(positions, portfolio_value)
    except Exception as e:
        return f"❌ Failed to fetch positions: {e}"
    finally:
        db.close()


# ── Health check (HTTP mode) ──────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "stock-signal-monitor-mcp"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="http", host="0.0.0.0", port=8001)
    else:
        mcp.run(transport="stdio")
