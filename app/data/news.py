"""Alpha Vantage news sentiment fetcher."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_AV_URL = "https://www.alphavantage.co/query"


async def get_ticker_sentiment(ticker: str) -> dict | None:
    """
    Fetch Alpha Vantage news sentiment for a ticker.

    Returns:
        {"bullish_pct": float, "bearish_pct": float, "score": float}
        or None if unavailable (no API key, request failed, or no data)
    """
    if not settings.alpha_vantage_api_key:
        return None

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "apikey": settings.alpha_vantage_api_key,
        "limit": 50,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_AV_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        feed = data.get("feed", [])
        if not feed:
            return None

        scores = []
        for article in feed:
            for ts in article.get("ticker_sentiment", []):
                if ts.get("ticker", "").upper() == ticker.upper():
                    try:
                        scores.append(float(ts["ticker_sentiment_score"]))
                    except (KeyError, ValueError):
                        pass

        if not scores:
            return None

        bullish_pct = sum(1 for s in scores if s > 0.15) / len(scores)
        bearish_pct = sum(1 for s in scores if s < -0.15) / len(scores)
        score = (sum(scores) / len(scores) + 1) / 2  # normalize -1..1 → 0..1

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
