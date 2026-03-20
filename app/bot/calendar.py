"""US economic & earnings calendar — Finnhub API + official Fed/BLS schedules."""
import logging
from datetime import date, timedelta

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Official 2026 FOMC meeting dates (published by Federal Reserve)
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# CPI release dates 2026 (published by BLS)
CPI_2026 = [
    "2026-01-14", "2026-02-12", "2026-03-11", "2026-04-14",
    "2026-05-12", "2026-06-10", "2026-07-15", "2026-08-12",
    "2026-09-11", "2026-10-13", "2026-11-12", "2026-12-10",
]

# Non-Farm Payrolls 2026 (first Friday of each month, BLS)
NFP_2026 = [
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]

ECON_EVENTS = (
    [(d, "🏦 FOMC 利率决议", "高") for d in FOMC_2026]
    + [(d, "📈 CPI 消费者物价指数", "高") for d in CPI_2026]
    + [(d, "👷 非农就业数据", "高") for d in NFP_2026]
)


async def _fetch_earnings(from_date: str, to_date: str, watchlist: list[str]) -> list[tuple[str, str, str]]:
    """Fetch earnings calendar from Finnhub, filtered by watchlist."""
    if not settings.finnhub_api_key:
        return []
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={from_date}&to={to_date}&token={settings.finnhub_api_key}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            data = resp.json()
        earnings = data.get("earningsCalendar", [])
        results = []
        watchlist_upper = {t.upper() for t in watchlist}
        for e in earnings:
            sym = e.get("symbol", "")
            if sym in watchlist_upper:
                eps_est = e.get("epsEstimate")
                rev_est = e.get("revenueEstimate")
                detail = f"EPS预估: ${eps_est}" if eps_est else ""
                if rev_est:
                    detail += f" | 营收预估: ${rev_est/1e9:.1f}B" if rev_est > 1e6 else ""
                results.append((e["date"], f"📊 {sym} 财报发布", detail or "待公布"))
        return results
    except Exception as e:
        logger.error(f"Finnhub earnings error: {e}")
        return []


async def get_upcoming_events(days: int = 14, watchlist: list[str] | None = None) -> str:
    """Get economic events + watchlist earnings within the next N days."""
    today = date.today()
    end = today + timedelta(days=days)
    from_str = today.isoformat()
    to_str = end.isoformat()

    # Collect economic events
    upcoming = []
    for date_str, event, impact in ECON_EVENTS:
        event_date = date.fromisoformat(date_str)
        if today <= event_date <= end:
            days_until = (event_date - today).days
            upcoming.append((event_date, days_until, f"{event} | 影响: {impact}"))

    # Fetch earnings for watchlist
    if watchlist:
        earnings = await _fetch_earnings(from_str, to_str, watchlist)
        for date_str, event, detail in earnings:
            event_date = date.fromisoformat(date_str)
            days_until = (event_date - today).days
            upcoming.append((event_date, days_until, f"{event} | {detail}"))

    if not upcoming:
        return f"📅 未来 {days} 天内无重大事件。"

    upcoming.sort(key=lambda x: x[0])

    lines = [f"📅 *美股大事日历* (未来{days}天)\n"]
    for event_date, days_until, text in upcoming:
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][event_date.weekday()]
        if days_until == 0:
            tag = "⚡今天"
        elif days_until == 1:
            tag = "🔜明天"
        elif days_until <= 3:
            tag = f"📌{days_until}天后"
        else:
            tag = f"{days_until}天后"
        lines.append(f"{tag} | {event_date.strftime('%m/%d')} {weekday} | {text}")

    return "\n".join(lines)
