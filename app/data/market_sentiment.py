"""Market sentiment composite: CNN Fear&Greed + VIX 30d slope + Finnhub news."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import numpy as np

logger = logging.getLogger(__name__)

_CACHE: dict = {"sentiment": None, "ts": 0.0}
_CACHE_TTL = 3600.0  # 1 hour

_CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


@dataclass
class MarketSentiment:
    fear_greed_score: int       # 0-100, CNN Fear & Greed
    fear_greed_label: str       # "Extreme Fear"/"Fear"/"Neutral"/"Greed"/"Extreme Greed"
    vix_30d_slope: float        # linear slope of VIX daily closes over 30 days (pts/day)
    finnhub_bullish_pct: float  # 0.0-1.0, avg bullish_pct across watchlist tickers
    composite_score: int        # aggregated 0-100 (higher = more bullish)


def _compute_composite(
    fear_greed: int,
    vix_slope: float,
    finnhub_bullish_pct: float,
) -> int:
    """
    Composite formula:
      fg_component      = fear_greed_score                        (0-100)
      vix_component     = clip(100 - (vix_slope * 80 + 50), 0, 100)  (inverted)
      finnhub_component = finnhub_bullish_pct * 100              (0-100)
      composite         = int(fg*0.5 + vix*0.3 + finnhub*0.2)

    VIX slope multiplier 80: slope=+0.625 → vix_component=0 (severe bear)
                              slope=-0.625 → vix_component=100 (strong bull)
                              slope=0      → vix_component=50  (neutral)
    """
    vix_component     = float(np.clip(100 - (vix_slope * 80 + 50), 0, 100))
    finnhub_component = finnhub_bullish_pct * 100
    raw = fear_greed * 0.5 + vix_component * 0.3 + finnhub_component * 0.2
    return int(raw)


async def _fetch_fear_greed() -> tuple[int, str]:
    """Fetch CNN Fear & Greed score. Returns (score, label), defaults (50, 'Neutral') on error."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_CNN_FG_URL)
            resp.raise_for_status()
            data = resp.json()
            fg_data = data.get("fear_and_greed", {})
            score = int(float(fg_data.get("score", 50)))
            label = str(fg_data.get("rating", "Neutral"))
            return score, label
    except Exception as e:
        logger.debug(f"Fear&Greed fetch failed: {e}")
        return 50, "Neutral"


async def _fetch_vix_slope() -> float:
    """Compute VIX 30-day linear slope (pts/day). Returns 0.0 on error."""
    try:
        import yfinance
        import pandas as pd
        df = yfinance.download("^VIX", period="40d", interval="1d", progress=False)
        if df is None or df.empty:
            return 0.0
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        closes = df["Close"].dropna().tail(30).values
        if len(closes) < 5:
            return 0.0
        x = np.arange(len(closes), dtype=float)
        slope, _ = np.polyfit(x, closes, 1)
        return float(slope)
    except Exception as e:
        logger.debug(f"VIX slope fetch failed: {e}")
        return 0.0


async def _fetch_finnhub_avg(tickers: list[str]) -> float:
    """Average bullish_pct from Finnhub news sentiment across tickers. Returns 0.5 on error."""
    if not tickers:
        return 0.5
    try:
        from app.data.news import get_ticker_sentiment
        tasks = [get_ticker_sentiment(t) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        values = [r["bullish_pct"] for r in results if isinstance(r, dict) and "bullish_pct" in r]
        return sum(values) / len(values) if values else 0.5
    except Exception as e:
        logger.debug(f"Finnhub avg failed: {e}")
        return 0.5


async def get_market_sentiment(tickers: list[str]) -> MarketSentiment:
    """
    Fetch all three sentiment signals concurrently. Returns neutral defaults on error.
    Results are cached for 1 hour to avoid redundant API calls within the same scan.

    Args:
        tickers: Watchlist tickers to aggregate Finnhub news sentiment.
    """
    now = time.time()
    if _CACHE["ts"] and now - _CACHE["ts"] < _CACHE_TTL and _CACHE["sentiment"] is not None:
        return _CACHE["sentiment"]

    fg_score, fg_label, vix_slope, finnhub_pct = 50, "Neutral", 0.0, 0.5
    try:
        (fg_score, fg_label), vix_slope, finnhub_pct = await asyncio.gather(
            _fetch_fear_greed(),
            _fetch_vix_slope(),
            _fetch_finnhub_avg(tickers),
        )
    except Exception as e:
        logger.warning(f"Market sentiment fetch error: {e}")

    composite = _compute_composite(fg_score, vix_slope, finnhub_pct)
    sentiment = MarketSentiment(
        fear_greed_score   = fg_score,
        fear_greed_label   = fg_label,
        vix_30d_slope      = vix_slope,
        finnhub_bullish_pct= finnhub_pct,
        composite_score    = composite,
    )
    _CACHE["sentiment"] = sentiment
    _CACHE["ts"] = now
    return sentiment
