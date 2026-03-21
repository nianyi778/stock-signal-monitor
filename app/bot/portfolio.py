"""Portfolio tracking: position entries, P&L calculation, sell recording."""
from datetime import UTC, datetime
from sqlalchemy.orm import Session
from app.models import PositionEntry


def _try_create_active_trade(db: Session, ticker: str, buy_price: float) -> None:
    """Best-effort: create ActiveTrade for a manually entered position.
    Uses current market data to calculate stop/target. Never blocks position recording."""
    try:
        from app.models import ActiveTrade
        # Skip if already monitored
        existing = db.query(ActiveTrade).filter_by(ticker=ticker, status="ACTIVE").first()
        if existing:
            return

        from app.data.fetcher import fetch_ohlcv
        from app.signals.engine import _calc_levels, _build_entry_exit
        df = fetch_ohlcv(ticker)
        if df is None or df.empty:
            return

        price = float(df["Close"].iloc[-1])
        support, resistance, atr = _calc_levels(df, price)

        if atr is None or atr <= 0:
            atr = price * 0.02
        stop = round(support - 1.5 * atr, 2)
        warn = round(stop + 0.75 * atr, 2)

        ent = _build_entry_exit(price, support, resistance, atr)
        target = ent["target_price"] if ent else resistance
        partial_tp = round(target * 0.95, 2) if target else None
        rr = ent["rr_ratio"] if ent else None

        # Fetch earnings date
        earnings_dt = None
        try:
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            if cal is not None:
                dates = cal.get("Earnings Date", [])
                if dates:
                    d = dates[0]
                    if hasattr(d, "year"):
                        earnings_dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
        except Exception:
            pass

        trade = ActiveTrade(
            ticker=ticker,
            entry_low=buy_price,
            entry_high=buy_price,
            target_price=target,
            stop_price=stop,
            warn_price=warn,
            partial_tp=partial_tp,
            rr_ratio=rr,
            atr_at_signal=atr,
            earnings_date=earnings_dt,
            status="ACTIVE",
            valid_until=None,  # manual positions don't expire
        )
        db.add(trade)
    except Exception:
        pass  # never block position recording


def add_position(db: Session, ticker: str, buy_price: float, shares: float, note: str = "") -> PositionEntry:
    ticker = ticker.upper()
    entry = PositionEntry(ticker=ticker, buy_price=buy_price, shares=shares, note=note)
    db.add(entry)

    # Auto-add to watchlist if not already there
    from app.models import WatchlistItem
    wl = db.query(WatchlistItem).filter_by(ticker=ticker).first()
    if wl:
        wl.is_active = True
    else:
        db.add(WatchlistItem(ticker=ticker, name=ticker))

    # Auto-create ActiveTrade for daily monitoring
    _try_create_active_trade(db, ticker, buy_price)

    db.commit()
    db.refresh(entry)
    return entry


def sell_position(db: Session, ticker: str, sell_price: float) -> dict:
    """Mark all active entries for ticker as sold. Returns P&L summary."""
    entries = db.query(PositionEntry).filter_by(ticker=ticker.upper(), is_active=True).all()
    if not entries:
        return {"error": f"{ticker} 无持仓记录"}
    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    pnl_usd = (sell_price - avg_price) * total_shares
    pnl_pct = (sell_price - avg_price) / avg_price * 100
    for e in entries:
        e.is_active = False
        e.sell_price = sell_price
        e.sold_at = datetime.now(UTC)
    db.commit()
    return {
        "ticker": ticker,
        "total_shares": total_shares,
        "avg_price": round(avg_price, 2),
        "sell_price": sell_price,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def get_positions_summary(db: Session, ticker: str, current_price: float) -> dict:
    entries = db.query(PositionEntry).filter_by(ticker=ticker.upper(), is_active=True).all()
    if not entries:
        return {"ticker": ticker, "total_shares": 0.0, "avg_price": 0.0,
                "current_price": current_price, "current_pnl_pct": 0.0,
                "current_pnl_usd": 0.0, "position_pct": None}
    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    pnl_usd = (current_price - avg_price) * total_shares
    pnl_pct = (current_price - avg_price) / avg_price * 100
    return {
        "ticker": ticker,
        "total_shares": total_shares,
        "avg_price": round(avg_price, 2),
        "current_price": current_price,
        "current_pnl_pct": round(pnl_pct, 2),
        "current_pnl_usd": round(pnl_usd, 2),
        "position_pct": None,
    }


def get_all_positions(db: Session) -> list[dict]:
    """Get all active tickers aggregate data (without fetching live prices)."""
    entries = db.query(PositionEntry).filter_by(is_active=True).all()
    tickers = list({e.ticker for e in entries})
    result = []
    for ticker in tickers:
        ticker_entries = [e for e in entries if e.ticker == ticker]
        total_shares = sum(e.shares for e in ticker_entries)
        avg_price = sum(e.buy_price * e.shares for e in ticker_entries) / total_shares
        result.append({
            "ticker": ticker,
            "total_shares": total_shares,
            "avg_price": round(avg_price, 2),
            "current_price": 0.0,
            "current_pnl_pct": 0.0,
            "current_pnl_usd": 0.0,
            "position_pct": None,
        })
    return result


def format_portfolio_message(positions: list[dict], portfolio_value: float = 0) -> str:
    if not positions:
        return "📭 暂无持仓记录。"
    lines = ["*💼 我的持仓*\n"]
    total_pnl = 0.0
    for p in positions:
        ticker = p["ticker"]
        shares = p["total_shares"]
        avg = p["avg_price"]
        curr = p.get("current_price", 0)
        pnl_pct = p["current_pnl_pct"]
        pnl_usd = p["current_pnl_usd"]
        total_pnl += pnl_usd
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        pct_str = f"{pnl_pct:+.1f}%"
        usd_str = f"${abs(pnl_usd):,.0f}"
        pos_pct_str = f"{p['position_pct']:.1f}%" if p.get("position_pct") else "—"
        lines.append(
            f"{emoji} *{ticker}*  {shares:.0f}股 @ \\${avg:.2f}\n"
            f"  现价 \\${curr:.2f}  {pct_str}（{'+'  if pnl_usd >= 0 else '-'}{usd_str}）  仓位 {pos_pct_str}"
        )
    if portfolio_value > 0:
        total_pct = total_pnl / portfolio_value * 100
        lines.append(f"\n📊 总浮盈亏: {'🟢' if total_pnl >= 0 else '🔴'} \\${total_pnl:+,.0f}（{total_pct:+.1f}%）")
    return "\n".join(lines)
