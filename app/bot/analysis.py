"""Stock analysis: price context, action recommendation, technicals, events."""
import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Market state labels
_MARKET_STATE = {
    "PRE": "🌅 盘前交易",
    "REGULAR": "🟢 盘中",
    "POST": "🌙 盘后交易",
    "CLOSED": "🔴 已收盘",
    "PREPRE": "🌑 深夜盘前",
    "POSTPOST": "🌑 深夜盘后",
}


def _md_safe(text: str) -> str:
    """Escape Markdown special chars in external data (company names, etc.)."""
    for ch in ("*", "_", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def _pct(a, b) -> str:
    """Format percentage change from b to a."""
    if not a or not b:
        return ""
    p = (a - b) / b * 100
    return f"{p:+.2f}%"


def _action_score(signals, rsi_val, price, ma50, ma200, analyst_bulls, analyst_bears, analyst_total) -> int:
    """Score -5..+5: positive = bullish, negative = bearish."""
    score = 0
    for s in signals:
        if s.signal_level == "STRONG":
            score += 3 if s.signal_type == "BUY" else -3
        elif s.signal_level == "WEAK":
            score += 1 if s.signal_type == "BUY" else -1
    if rsi_val is not None:
        if rsi_val < 25:
            score += 2
        elif rsi_val < 35:
            score += 1
        elif rsi_val > 75:
            score -= 2
        elif rsi_val > 65:
            score -= 1
    if price and ma50:
        score += 0.5 if price > ma50 else -0.5
    if price and ma200:
        score += 0.5 if price > ma200 else -0.5
    if analyst_total and analyst_total > 0:
        bull_ratio = analyst_bulls / analyst_total
        if bull_ratio > 0.6:
            score += 1
        elif bull_ratio < 0.3:
            score -= 1
    return score


def _build_action(score: float, price: float, support: float | None, resistance: float | None, hist=None) -> list[str]:
    """Produce actionable recommendation lines."""
    lines = []
    if score >= 3:
        action = "🟢 *加仓*"
        reason = "多指标共振看涨"
    elif score >= 1.5:
        action = "🟢 *小仓加仓*"
        reason = "偏多信号，可轻仓参与"
    elif score <= -3:
        action = "🔴 *减仓 / 止损*"
        reason = "多指标共振看跌"
    elif score <= -1.5:
        action = "🔴 *考虑减仓*"
        reason = "偏空信号，注意风险"
    else:
        action = "🟡 *观望*"
        reason = "信号分歧，等待确认"

    lines.append(f"  {action} — {reason}")

    if price:
        if score >= 1.5 and support:
            buy_low = round(support * 1.002, 2)
            buy_high = round(price * 1.01, 2)
            # ATR-based stop: 2×ATR below current price, but must be below support
            try:
                from app.signals.indicators import calc_atr
                close_s = pd.Series([float(v) for v in hist["Close"].values])
                high_s  = pd.Series([float(v) for v in hist["High"].values])
                low_s   = pd.Series([float(v) for v in hist["Low"].values])
                atr_series = calc_atr(high_s, low_s, close_s)
                atr_val = float(atr_series.dropna().iloc[-1]) if atr_series.dropna().shape[0] > 0 else price * 0.02
            except Exception:
                atr_val = price * 0.02
            stop = round(min(price - 2 * atr_val, support - 0.3 * atr_val), 2)
            lines.append(f"  📥 买入区间: ${buy_low:.2f} ~ ${buy_high:.2f}")
            lines.append(f"  🛑 止损参考: ${stop:.2f}（ATR 止损）")
            if resistance:
                upside = _pct(resistance, price)
                lines.append(f"  🎯 目标阻力: ${resistance:.2f}（{upside}）")
        elif score <= -1.5 and resistance:
            sell_low = round(price * 0.99, 2)
            sell_high = round(resistance * 0.998, 2)
            lines.append(f"  📤 减仓区间: ${sell_low:.2f} ~ ${sell_high:.2f}")
            if support:
                downside = _pct(support, price)
                lines.append(f"  ⚠️ 下方支撑: ${support:.2f}（{downside}）")

    return lines


def get_stock_analysis(ticker: str) -> str:
    """Generate a comprehensive, actionable stock analysis message."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception as e:
        logger.error(f"Failed to fetch info for {ticker}: {e}")
        return f"❌ 无法获取 {ticker} 数据"

    name = _md_safe(info.get("shortName") or info.get("longName") or ticker)
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    day_change = info.get("regularMarketChangePercent")
    volume = info.get("regularMarketVolume")
    avg_volume = info.get("averageVolume")
    market_state = info.get("marketState", "CLOSED")
    market_cap = info.get("marketCap")

    lines = [f"📊 *{ticker}* — {name}"]
    if market_cap:
        cap_str = f"${market_cap/1e9:.1f}B" if market_cap >= 1e9 else f"${market_cap/1e6:.0f}M"
        lines[0] += f"  _{cap_str}_"
    lines.append("")

    # ── 1. 价格概况 ────────────────────────────────
    lines.append("*💰 价格概况*")
    state_label = _MARKET_STATE.get(market_state, market_state)
    lines.append(f"  {state_label}")

    if price:
        day_chg_str = f"（{day_change:+.2f}%）" if day_change is not None else (
            f"（{_pct(price, prev_close)}）" if prev_close else "")
        lines.append(f"  • 价格: *${price:.2f}*  {day_chg_str}")

    # Pre-market
    pre_price = info.get("preMarketPrice")
    pre_chg = info.get("preMarketChangePercent")
    if pre_price:
        pre_str = f"${pre_price:.2f}"
        if pre_chg is not None:
            pre_str += f"  {pre_chg:+.2f}%"
        elif price:
            pre_str += f"  {_pct(pre_price, price)}"
        lines.append(f"  • 盘前: {pre_str}")

    # After-hours
    post_price = info.get("postMarketPrice")
    post_chg = info.get("postMarketChangePercent")
    if post_price:
        post_str = f"${post_price:.2f}"
        if post_chg is not None:
            post_str += f"  {post_chg:+.2f}%"
        elif price:
            post_str += f"  {_pct(post_price, price)}"
        lines.append(f"  • 盘后: {post_str}")

    # Volume vs average
    if volume and avg_volume and avg_volume > 0:
        vol_ratio = volume / avg_volume
        vol_tag = " 🔥量能放大" if vol_ratio > 1.5 else (" ⚠️量能萎缩" if vol_ratio < 0.5 else "")
        lines.append(f"  • 成交量: {volume/1e6:.1f}M（均量 {avg_volume/1e6:.1f}M，{vol_ratio:.1f}x）{vol_tag}")

    high_52 = info.get("fiftyTwoWeekHigh")
    low_52 = info.get("fiftyTwoWeekLow")
    if high_52 and low_52 and price:
        pos_pct = (price - low_52) / (high_52 - low_52) * 100 if high_52 != low_52 else 50
        lines.append(f"  • 52周区间: ${low_52:.2f} ~ ${high_52:.2f}  （当前位于 {pos_pct:.0f}%）")

    # ── 2. 技术指标 & 运行引擎 ──────────────────────
    ma50 = info.get("fiftyDayAverage")
    ma200 = info.get("twoHundredDayAverage")
    close = None
    bb_upper = bb_mid = bb_lower = None
    recent_high = recent_low = None
    rsi_val = None

    try:
        import pandas_ta as ta
        hist = yf.download(ticker, period="3mo", progress=False)
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.droplevel(1)
        close = hist["Close"]
        high_ser = hist["High"]
        low_ser = hist["Low"]
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None:
            cols = bb.columns
            bb_upper = float(bb[[c for c in cols if "BBU" in c][0]].iloc[-1])
            bb_mid = float(bb[[c for c in cols if "BBM" in c][0]].iloc[-1])
            bb_lower = float(bb[[c for c in cols if "BBL" in c][0]].iloc[-1])
        recent_high = float(high_ser.tail(20).max())
        recent_low = float(low_ser.tail(20).min())
        rsi_series = ta.rsi(close, length=14)
        if rsi_series is not None:
            rsi_val = float(rsi_series.iloc[-1])
    except Exception as e:
        logger.warning(f"Technical calc error for {ticker}: {e}")

    # Run live signal engine
    live_signals = []
    try:
        from app.signals.engine import run_signals
        live_signals = run_signals(ticker)
    except Exception as e:
        logger.warning(f"Signal engine error for {ticker}: {e}")

    # ── 3. 操作建议 ────────────────────────────────
    lines.append("\n*🎯 操作建议*")

    analyst_bulls = analyst_bears = analyst_total = 0
    try:
        rec = t.recommendations
        if rec is not None and len(rec) > 0:
            latest = rec.iloc[0]
            analyst_bulls = latest.get("strongBuy", 0) + latest.get("buy", 0)
            analyst_bears = latest.get("sell", 0) + latest.get("strongSell", 0)
            analyst_total = analyst_bulls + latest.get("hold", 0) + analyst_bears
    except Exception:
        pass

    score = _action_score(live_signals, rsi_val, price, ma50, ma200,
                          analyst_bulls, analyst_bears, analyst_total)

    # Nearest support and resistance
    all_levels = []
    for val, label in [
        (bb_lower, "布林下轨"), (recent_low, "20日低点"), (ma50, "50日均"),
        (ma200, "200日均"), (bb_mid, "布林中轨"), (bb_upper, "布林上轨"),
        (recent_high, "20日高点"), (high_52, "52周高"), (low_52, "52周低"),
    ]:
        if val:
            all_levels.append(val)

    nearest_support = max((v for v in all_levels if v < price), default=None) if price else None
    nearest_resist = min((v for v in all_levels if v > price), default=None) if price else None

    action_lines = _build_action(score, price, nearest_support, nearest_resist, hist=hist if close is not None else None)
    lines.extend(action_lines)

    # Show active signals
    if live_signals:
        lines.append("")
        for s in live_signals:
            sig_emoji = "🟢" if s.signal_type == "BUY" else ("🔴" if s.signal_type == "SELL" else "🟡")
            level_tag = "⚡强" if s.signal_level == "STRONG" else ""
            lines.append(f"  {sig_emoji}{level_tag} {s.indicator}: {s.message}（{s.confidence}%）")
    else:
        lines.append("  _暂无明确技术信号_")

    # ── 4. 支撑 / 阻力位 ────────────────────────────
    lines.append("\n*📐 支撑 / 阻力位*")
    levels = []
    for label, val in [
        ("52周高点", high_52), ("布林上轨", bb_upper), ("20日高点", recent_high),
        ("50日均线", ma50), ("布林中轨", bb_mid), ("200日均线", ma200),
        ("20日低点", recent_low), ("布林下轨", bb_lower), ("52周低点", low_52),
    ]:
        if val:
            levels.append((label, val))
    levels.sort(key=lambda x: x[1], reverse=True)
    price_inserted = False
    for label, val in levels:
        if not price_inserted and price and price >= val:
            lines.append(f"  ▶️ *当前 ${price:.2f}*")
            price_inserted = True
        marker = " 🔺阻力" if price and val > price else " 🔻支撑"
        lines.append(f"  • {label}: ${val:.2f}{marker}")
    if not price_inserted and price:
        lines.append(f"  ▶️ *当前 ${price:.2f}*")

    # ── 5. 市场情绪 ─────────────────────────────────
    lines.append("\n*🧠 市场情绪*")

    if rsi_val is not None:
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

    if ma50 and ma200:
        trend = "多头排列 🟢" if ma50 > ma200 else "空头排列 🔴"
        lines.append(f"  • 均线: MA50({ma50:.2f}) vs MA200({ma200:.2f}) — {trend}")

    if analyst_total > 0:
        bull_ratio = analyst_bulls / analyst_total * 100
        lines.append(
            f"  • 分析师({analyst_total}人): {analyst_bulls}买 / "
            f"{analyst_total - analyst_bulls - analyst_bears}持有 / {analyst_bears}卖 "
            f"（看多 {bull_ratio:.0f}%）"
        )

    # Short interest / beta
    short_pct = info.get("shortPercentOfFloat")
    beta = info.get("beta")
    if short_pct and short_pct > 0.05:
        lines.append(f"  • 空头持仓: {short_pct*100:.1f}%{'  ⚠️高空头' if short_pct > 0.15 else ''}")
    if beta:
        lines.append(f"  • Beta(波动系数): {beta:.2f}{'  ⚡高波动' if beta > 1.5 else ''}")

    # ── 6. 近期大事预警 ─────────────────────────────
    lines.append("\n*⚠️ 近期大事预警*")
    events_added = 0

    # Earnings from yfinance calendar
    try:
        cal = t.calendar
        if cal:
            earnings_dates = cal.get("Earnings Date", [])
            if earnings_dates:
                next_date = earnings_dates[0]
                if isinstance(next_date, date):
                    days_until = (next_date - date.today()).days
                    urgency = "🔴" if days_until <= 7 else ("🟡" if days_until <= 21 else "📅")
                    lines.append(f"  {urgency} 财报: {next_date}（{days_until}天后）")
                    events_added += 1

                    eps_avg = cal.get("Earnings Average")
                    eps_low = cal.get("Earnings Low")
                    eps_high = cal.get("Earnings High")
                    if eps_avg is not None:
                        lines.append(f"      预估 EPS: ${eps_avg:.3f}（区间 ${eps_low:.2f} ~ ${eps_high:.2f}）")

                    rev_avg = cal.get("Revenue Average")
                    rev_low = cal.get("Revenue Low")
                    rev_high = cal.get("Revenue High")
                    if rev_avg is not None:
                        lines.append(
                            f"      预估营收: ${rev_avg/1e9:.2f}B（${rev_low/1e9:.2f}B ~ ${rev_high/1e9:.2f}B）"
                        )

            # EPS growth estimate
            try:
                est = t.earnings_estimate
                if est is not None and "+1y" in est.index:
                    growth = est.loc["+1y"].get("growth")
                    if growth is not None and not pd.isna(growth):
                        lines.append(f"      明年 EPS 增速预估: {growth*100:+.1f}%")
            except Exception:
                pass
    except Exception:
        pass

    # Macro events from DB (FOMC / CPI / NFP in next 30 days)
    try:
        from app.database import SessionLocal
        from app.models import EconomicEvent
        db = SessionLocal()
        try:
            today_dt = datetime.now(UTC)
            end_dt = today_dt + timedelta(days=30)
            macro_events = (
                db.query(EconomicEvent)
                .filter(
                    EconomicEvent.event_date >= today_dt,
                    EconomicEvent.event_date <= end_dt,
                    EconomicEvent.impact == "高",
                    EconomicEvent.ticker.is_(None),  # macro only, not earnings
                )
                .order_by(EconomicEvent.event_date)
                .limit(4)
                .all()
            )
            for ev in macro_events:
                ev_date = ev.event_date.date() if isinstance(ev.event_date, datetime) else ev.event_date
                days_until = (ev_date - date.today()).days
                urgency = "🔴" if days_until <= 3 else ("🟡" if days_until <= 7 else "📅")
                lines.append(f"  {urgency} {ev.title}: {ev_date}（{days_until}天后）")
                events_added += 1
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"DB event fetch error: {e}")

    if events_added == 0:
        lines.append("  _未来 30 天内无重大事件_")

    return "\n".join(lines)
