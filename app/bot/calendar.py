"""Economic calendar: crawl → DB → display. Refreshed daily by scheduler."""
import logging
from datetime import UTC, date, datetime, timedelta

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import EconomicEvent, WatchlistItem

logger = logging.getLogger(__name__)

# --- Official 2026 schedules (updated annually) ---
# These are published by the Fed and BLS — deterministic, not guesses.

_OFFICIAL_EVENTS = [
    # FOMC (Federal Reserve)
    *[{"date": d, "type": "FOMC", "title": "🏦 FOMC 利率决议", "impact": "高", "source": "fed"}
      for d in ["2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
                "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"]],
    # CPI (BLS)
    *[{"date": d, "type": "CPI", "title": "📈 CPI 消费者物价指数", "impact": "高", "source": "bls"}
      for d in ["2026-01-14", "2026-02-12", "2026-03-11", "2026-04-14",
                "2026-05-12", "2026-06-10", "2026-07-15", "2026-08-12",
                "2026-09-11", "2026-10-13", "2026-11-12", "2026-12-10"]],
    # Non-Farm Payrolls (BLS, first Friday)
    *[{"date": d, "type": "NFP", "title": "👷 非农就业数据", "impact": "高", "source": "bls"}
      for d in ["2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
                "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
                "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04"]],
    # GDP (BEA)
    *[{"date": d, "type": "GDP", "title": "🇺🇸 GDP 初值", "impact": "高", "source": "bea"}
      for d in ["2026-01-29", "2026-04-29", "2026-07-29", "2026-10-29"]],
    # PCE (BEA, Fed's preferred inflation gauge)
    *[{"date": d, "type": "PCE", "title": "🎯 PCE 物价指数", "impact": "中", "source": "bea"}
      for d in ["2026-01-30", "2026-02-27", "2026-03-27", "2026-04-30",
                "2026-05-29", "2026-06-26", "2026-07-31", "2026-08-28",
                "2026-09-25", "2026-10-30", "2026-11-25", "2026-12-23"]],
]


def _sync_official_events(db) -> int:
    """Upsert official economic events into DB. Returns count added."""
    count = 0
    for e in _OFFICIAL_EVENTS:
        event_date = datetime.fromisoformat(e["date"]).replace(tzinfo=UTC)
        exists = db.query(EconomicEvent).filter(
            EconomicEvent.event_date == event_date,
            EconomicEvent.event_type == e["type"],
        ).first()
        if not exists:
            db.add(EconomicEvent(
                event_date=event_date,
                event_type=e["type"],
                title=e["title"],
                impact=e["impact"],
                source=e["source"],
            ))
            count += 1
    if count:
        db.commit()
    return count


def _fmt_econ_value(val, unit: str) -> str:
    """Format economic indicator value with its unit."""
    if val is None:
        return "—"
    unit = (unit or "").strip()
    if unit == "%":
        return f"{val:.2f}%"
    if unit in ("K", "k"):
        return f"{val:.0f}K"
    if unit in ("B", "b") or (abs(val) > 1e8 and not unit):
        return f"${val/1e9:.2f}B"
    return f"{val:.2f}{(' ' + unit) if unit else ''}"


# Keywords to match Finnhub event names → our event types
_ECON_KEYWORDS: dict[str, list[str]] = {
    "CPI":  ["consumer price index", "cpi"],
    "NFP":  ["nonfarm payroll", "non farm payroll", "non-farm payroll"],
    "PCE":  ["pce", "personal consumption expenditure", "personal spending"],
    "FOMC": ["fed interest rate", "federal funds rate", "fomc rate", "interest rate decision"],
    "GDP":  ["gross domestic product", "gdp"],
}


def _match_event_type(event_name: str) -> str | None:
    name_lower = event_name.lower()
    for etype, keywords in _ECON_KEYWORDS.items():
        if any(k in name_lower for k in keywords):
            return etype
    return None


def _sync_finnhub_macro(db) -> int:
    """Fetch macro economic events from Finnhub and enrich stored events with forecast/prior."""
    if not settings.finnhub_api_key:
        return 0

    today = date.today()
    from_str = today.isoformat()
    to_str = (today + timedelta(days=60)).isoformat()

    try:
        resp = httpx.get(
            f"https://finnhub.io/api/v1/calendar/economic?from={from_str}&to={to_str}&token={settings.finnhub_api_key}",
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"Finnhub economic calendar fetch error: {e}")
        return 0

    updated = 0
    for item in data.get("economicCalendar", []):
        if item.get("country", "").upper() != "US":
            continue
        etype = _match_event_type(item.get("event", ""))
        if not etype:
            continue

        # Parse event datetime (Finnhub returns ISO string)
        try:
            raw_time = item.get("time", "")
            if raw_time:
                event_dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            else:
                continue
        except Exception:
            continue

        # Find matching stored event within ±1 day
        window_start = event_dt - timedelta(days=1)
        window_end = event_dt + timedelta(days=1)
        stored = db.query(EconomicEvent).filter(
            EconomicEvent.event_type == etype,
            EconomicEvent.event_date >= window_start,
            EconomicEvent.event_date <= window_end,
            EconomicEvent.ticker.is_(None),
        ).first()

        if not stored:
            continue

        unit = item.get("unit", "")
        estimate = item.get("estimate")
        prev = item.get("prev")
        actual = item.get("actual")

        parts = []
        if actual is not None:
            parts.append(f"实际: {_fmt_econ_value(actual, unit)}")
        if estimate is not None:
            parts.append(f"预期: {_fmt_econ_value(estimate, unit)}")
        if prev is not None:
            parts.append(f"前值: {_fmt_econ_value(prev, unit)}")

        if parts:
            new_detail = " | ".join(parts)
            if stored.detail != new_detail:
                stored.detail = new_detail
                stored.updated_at = datetime.now(UTC)
                updated += 1

    if updated:
        db.commit()
    return updated


def _sync_finnhub_earnings(db) -> int:
    """Fetch watchlist earnings from Finnhub and upsert into DB."""
    if not settings.finnhub_api_key:
        return 0

    watchlist = [i.ticker for i in db.query(WatchlistItem).filter(WatchlistItem.is_active == True).all()]
    if not watchlist:
        return 0

    today = date.today()
    from_str = today.isoformat()
    to_str = (today + timedelta(days=90)).isoformat()

    try:
        resp = httpx.get(
            f"https://finnhub.io/api/v1/calendar/earnings?from={from_str}&to={to_str}&token={settings.finnhub_api_key}",
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        logger.error(f"Finnhub fetch error: {e}")
        return 0

    watchlist_set = {t.upper() for t in watchlist}
    count = 0
    for e in data.get("earningsCalendar", []):
        sym = e.get("symbol", "")
        if sym not in watchlist_set:
            continue
        event_date = datetime.fromisoformat(e["date"]).replace(tzinfo=UTC)
        exists = db.query(EconomicEvent).filter(
            EconomicEvent.event_date == event_date,
            EconomicEvent.event_type == "EARNINGS",
            EconomicEvent.ticker == sym,
        ).first()

        eps_est = e.get("epsEstimate")
        rev_est = e.get("revenueEstimate")
        detail = ""
        if eps_est:
            detail += f"EPS预估: ${eps_est}"
        if rev_est and rev_est > 1e6:
            detail += f" | 营收预估: ${rev_est / 1e9:.1f}B"

        if exists:
            exists.detail = detail or exists.detail
            exists.updated_at = datetime.now(UTC)
        else:
            db.add(EconomicEvent(
                event_date=event_date,
                event_type="EARNINGS",
                title=f"📊 {sym} 财报发布",
                detail=detail or "待公布",
                impact="高",
                source="finnhub",
                ticker=sym,
            ))
            count += 1
    db.commit()  # always commit — captures both inserts and detail updates
    return count


def refresh_calendar() -> dict:
    """Crawl all sources and sync to DB. Called by scheduler daily."""
    db = SessionLocal()
    try:
        official = _sync_official_events(db)
        macro_enriched = _sync_finnhub_macro(db)
        earnings = _sync_finnhub_earnings(db)
        logger.info(
            f"Calendar refreshed: {official} official added, "
            f"{macro_enriched} macro enriched with forecast/prior, "
            f"{earnings} earnings added"
        )
        return {"official": official, "macro_enriched": macro_enriched, "earnings": earnings}
    finally:
        db.close()


def get_upcoming_events_from_db(days: int = 14) -> str:
    """Read upcoming events from DB."""
    db = SessionLocal()
    try:
        today = datetime.now(UTC)
        end = today + timedelta(days=days)
        events = (
            db.query(EconomicEvent)
            .filter(EconomicEvent.event_date >= today, EconomicEvent.event_date <= end)
            .order_by(EconomicEvent.event_date)
            .all()
        )
        if not events:
            return f"📅 未来 {days} 天内无重大事件。\n（提示：首次使用请点 📡 立即扫描 触发日历同步）"

        lines = [f"📅 *美股大事日历* (未来{days}天)\n"]
        for e in events:
            event_date = e.event_date.date() if isinstance(e.event_date, datetime) else e.event_date
            days_until = (event_date - date.today()).days
            weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][event_date.weekday()]

            if days_until == 0:
                tag = "⚡今天"
            elif days_until == 1:
                tag = "🔜明天"
            elif days_until <= 3:
                tag = f"📌{days_until}天后"
            else:
                tag = f"{days_until}天后"

            lines.append(f"{tag} | {event_date.strftime('%m/%d')} {weekday} | {e.title}")
            if e.detail:
                lines.append(f"  _{e.detail}_")

        return "\n".join(lines)
    finally:
        db.close()
