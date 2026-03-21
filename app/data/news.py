"""Finnhub news sentiment fetcher."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def get_ticker_sentiment(ticker: str) -> dict | None:
    """
    Fetch Finnhub news sentiment for a ticker.

    Returns:
        {"bullish_pct": float, "bearish_pct": float, "score": float}
        or None if unavailable (no API key, request failed, or no data)
    """
    if not settings.finnhub_api_key:
        return None

    url = "https://finnhub.io/api/v1/news-sentiment"
    params = {"symbol": ticker, "token": settings.finnhub_api_key}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        sentiment = data.get("sentiment", {})
        bullish_pct = float(sentiment.get("bullishPercent", 0.5))
        bearish_pct = float(sentiment.get("bearishPercent", 0.5))
        score = float(data.get("companyNewsScore", 0.5))
        return {"bullish_pct": bullish_pct, "bearish_pct": bearish_pct, "score": score}
    except Exception as e:
        logger.debug(f"News sentiment fetch failed for {ticker}: {e}")
        return None


def apply_sentiment_to_confidence(confidence: int, sentiment: dict | None) -> int:
    """
    Adjust signal confidence based on news sentiment.

    Rules:
        bullish_pct > 0.65  → +5  (cap 95)
        0.35–0.65           → no change (neutral zone)
        bullish_pct < 0.35  → -10 (floor 0)
    """
    if sentiment is None:
        return confidence
    bullish = sentiment["bullish_pct"]
    if bullish > 0.65:
        return min(confidence + 5, 95)
    elif bullish < 0.35:
        return max(confidence - 10, 0)
    return confidence
