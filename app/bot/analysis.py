"""Stock analysis: fundamentals, technicals, earnings, sentiment."""
import logging
from datetime import date

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def get_stock_analysis(ticker: str) -> str:
    """Generate a comprehensive stock analysis message."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception as e:
        logger.error(f"Failed to fetch info for {ticker}: {e}")
        return f"❌ 无法获取 {ticker} 数据"

    name = info.get("shortName") or info.get("longName") or ticker
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

    lines = [f"📊 *{ticker}* — {name}", f"💰 当前价格: ${price:.2f}\n"]

    # --- 支撑/阻力位 ---
    lines.append("*🔹 支撑 / 阻力位*")

    high_52 = info.get("fiftyTwoWeekHigh")
    low_52 = info.get("fiftyTwoWeekLow")
    ma50 = info.get("fiftyDayAverage")
    ma200 = info.get("twoHundredDayAverage")

    # Bollinger bands from recent data
    close = None
    try:
        import pandas_ta as ta
        hist = yf.download(ticker, period="3mo", progress=False)
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.droplevel(1)
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None:
            cols = bb.columns
            bb_upper = float(bb[[c for c in cols if "BBU" in c][0]].iloc[-1])
            bb_mid = float(bb[[c for c in cols if "BBM" in c][0]].iloc[-1])
            bb_lower = float(bb[[c for c in cols if "BBL" in c][0]].iloc[-1])
        else:
            bb_upper = bb_mid = bb_lower = None
        recent_high = float(high.tail(20).max())
        recent_low = float(low.tail(20).min())
    except Exception:
        bb_upper = bb_mid = bb_lower = None
        recent_high = recent_low = None

    # Build support/resistance table
    levels = []
    if high_52:
        levels.append(("52周高点", high_52))
    if bb_upper:
        levels.append(("布林上轨", bb_upper))
    if recent_high:
        levels.append(("20日高点", recent_high))
    if ma50:
        levels.append(("50日均线", ma50))
    if bb_mid:
        levels.append(("布林中轨", bb_mid))
    if ma200:
        levels.append(("200日均线", ma200))
    if recent_low:
        levels.append(("20日低点", recent_low))
    if bb_lower:
        levels.append(("布林下轨", bb_lower))
    if low_52:
        levels.append(("52周低点", low_52))

    # Sort by value descending, mark current price position
    levels.sort(key=lambda x: x[1], reverse=True)
    price_inserted = False
    for label, val in levels:
        marker = ""
        if not price_inserted and price >= val:
            lines.append(f"  ▶️ *当前 ${price:.2f}*")
            price_inserted = True
        if val > price:
            marker = " 🔺阻力"
        else:
            marker = " 🔻支撑"
        lines.append(f"  • {label}: ${val:.2f}{marker}")
    if not price_inserted:
        lines.append(f"  ▶️ *当前 ${price:.2f}*")

    # --- 市场情绪 ---
    lines.append("\n*🔹 市场情绪*")

    # RSI
    try:
        import pandas_ta as ta
        if close is None:
            raise ValueError("close data unavailable")
        rsi_val = float(ta.rsi(close, length=14).iloc[-1])
        if rsi_val > 70:
            rsi_label = "超买 ⚠️"
        elif rsi_val < 30:
            rsi_label = "超卖 ⚠️"
        elif rsi_val > 60:
            rsi_label = "偏多"
        elif rsi_val < 40:
            rsi_label = "偏空"
        else:
            rsi_label = "中性"
        lines.append(f"  • RSI(14): {rsi_val:.1f} — {rsi_label}")
    except Exception:
        pass

    # MA trend
    if ma50 and ma200:
        if ma50 > ma200:
            lines.append(f"  • 均线趋势: 50日 > 200日 — 多头排列 🟢")
        else:
            lines.append(f"  • 均线趋势: 50日 < 200日 — 空头排列 🔴")

    # Analyst recommendations
    try:
        rec = t.recommendations
        if rec is not None and len(rec) > 0:
            latest = rec.iloc[0]
            total = latest.get("strongBuy", 0) + latest.get("buy", 0) + latest.get("hold", 0) + latest.get("sell", 0) + latest.get("strongSell", 0)
            if total > 0:
                bulls = latest.get("strongBuy", 0) + latest.get("buy", 0)
                bears = latest.get("sell", 0) + latest.get("strongSell", 0)
                lines.append(f"  • 分析师: {bulls}买 / {latest.get('hold', 0)}持有 / {bears}卖 (共{total}人)")
    except Exception:
        pass

    # --- 财报 ---
    lines.append("\n*🔹 财报*")
    try:
        cal = t.calendar
        if cal:
            earnings_dates = cal.get("Earnings Date", [])
            if earnings_dates:
                next_date = earnings_dates[0]
                if isinstance(next_date, date):
                    days_until = (next_date - date.today()).days
                    lines.append(f"  • 下次财报: {next_date} ({days_until}天后)")
                else:
                    lines.append(f"  • 下次财报: {next_date}")

            eps_avg = cal.get("Earnings Average")
            eps_high = cal.get("Earnings High")
            eps_low = cal.get("Earnings Low")
            if eps_avg is not None:
                lines.append(f"  • 预估 EPS: ${eps_avg:.3f} (${eps_low:.2f} ~ ${eps_high:.2f})")

            rev_avg = cal.get("Revenue Average")
            rev_high = cal.get("Revenue High")
            rev_low = cal.get("Revenue Low")
            if rev_avg is not None:
                lines.append(f"  • 预估营收: ${rev_avg/1e9:.2f}B (${rev_low/1e9:.2f}B ~ ${rev_high/1e9:.2f}B)")
    except Exception:
        lines.append("  • 财报数据暂不可用")

    # Earnings estimates
    try:
        est = t.earnings_estimate
        if est is not None and len(est) > 0:
            if "+1y" in est.index:
                next_yr = est.loc["+1y"]
                growth = next_yr.get("growth")
                if growth is not None and not pd.isna(growth):
                    lines.append(f"  • 明年 EPS 增速预估: {growth*100:+.1f}%")
    except Exception:
        pass

    return "\n".join(lines)
