"""Tests for market sentiment composite score."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import pandas as pd
import numpy as np


@pytest.fixture(autouse=True)
def clear_sentiment_cache():
    from app.data import market_sentiment as ms
    ms._CACHE["sentiment"] = None
    ms._CACHE["ts"] = 0.0
    yield


@pytest.mark.asyncio
async def test_composite_score_neutral():
    """Fear=50, VIX slope=0, Finnhub=0.5 → composite ~50."""
    import httpx

    mock_fg_resp = MagicMock(spec=httpx.Response)
    mock_fg_resp.json.return_value = {"fear_and_greed": {"score": 50, "rating": "Neutral"}}
    mock_fg_resp.raise_for_status = MagicMock()

    # VIX flat: 30 daily closes all = 20
    vix_data = pd.DataFrame({"Close": [20.0] * 30})

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", return_value=vix_data):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_fg_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment(["AAPL"])

    # slope ≈ 0 → vix_component ≈ 50; fg=50; finnhub default 0.5 → composite ≈ 50
    assert 40 <= result.composite_score <= 60
    assert result.fear_greed_score == 50


@pytest.mark.asyncio
async def test_composite_score_extreme_fear():
    """Fear=15 (Extreme Fear) → composite < 40."""
    import httpx

    mock_fg_resp = MagicMock(spec=httpx.Response)
    mock_fg_resp.json.return_value = {"fear_and_greed": {"score": 15, "rating": "Extreme Fear"}}
    mock_fg_resp.raise_for_status = MagicMock()

    vix_rising = pd.DataFrame({"Close": [15.0 + i * 0.5 for i in range(30)]})  # slope ~0.5/day

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", return_value=vix_rising):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_fg_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment([])

    assert result.fear_greed_score == 15
    assert result.fear_greed_label == "Extreme Fear"
    assert result.composite_score < 40


@pytest.mark.asyncio
async def test_error_returns_neutral_defaults():
    """CNN endpoint fails → returns neutral defaults, no exception raised."""
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("yfinance.download", side_effect=Exception("network error")):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.data.market_sentiment import get_market_sentiment
        result = await get_market_sentiment(["AAPL"])

    assert result.composite_score == 50   # neutral default
    assert result.fear_greed_score == 50


def test_composite_formula():
    """Verify composite_score formula manually."""
    from app.data.market_sentiment import _compute_composite
    # fg=70, slope=-0.2 (falling VIX → bullish), finnhub_bullish=0.7
    # vix_component = clip(100 - (-0.2 * 80 + 50), 0, 100) = clip(100 - 34, 0, 100) = 66
    # composite = int(70*0.5 + 66*0.3 + 70*0.2) = int(35 + 19.8 + 14) = int(68.8) = 68
    score = _compute_composite(fear_greed=70, vix_slope=-0.2, finnhub_bullish_pct=0.7)
    assert score == 68
