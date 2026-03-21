"""Tests for Finnhub news sentiment fetcher."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.news import apply_sentiment_to_confidence, get_ticker_sentiment


class TestGetTickerSentiment:
    @pytest.mark.asyncio
    async def test_returns_none_without_finnhub_key(self):
        with patch("app.data.news.settings") as mock_settings:
            mock_settings.finnhub_api_key = ""
            result = await get_ticker_sentiment("AAPL")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_sentiment_dict_on_success(self):
        finnhub_response = {
            "sentiment": {"bullishPercent": 0.72, "bearishPercent": 0.28},
            "companyNewsScore": 0.68,
            "symbol": "AAPL",
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = finnhub_response

        with patch("app.data.news.settings") as mock_settings, \
             patch("app.data.news.httpx.AsyncClient") as mock_client_cls:
            mock_settings.finnhub_api_key = "test-key"
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await get_ticker_sentiment("AAPL")

        assert result is not None
        assert result["bullish_pct"] == pytest.approx(0.72)
        assert result["bearish_pct"] == pytest.approx(0.28)
        assert result["score"] == pytest.approx(0.68)

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        with patch("app.data.news.settings") as mock_settings, \
             patch("app.data.news.httpx.AsyncClient") as mock_client_cls:
            mock_settings.finnhub_api_key = "test-key"
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=ConnectionError("timeout"))
            mock_client_cls.return_value = mock_ctx

            result = await get_ticker_sentiment("AAPL")
        assert result is None


class TestApplySentimentToConfidence:
    def test_no_change_when_sentiment_none(self):
        assert apply_sentiment_to_confidence(75, None) == 75

    def test_boost_when_bullish(self):
        sentiment = {"bullish_pct": 0.70, "bearish_pct": 0.30, "score": 0.65}
        assert apply_sentiment_to_confidence(80, sentiment) == 85

    def test_cap_at_95(self):
        sentiment = {"bullish_pct": 0.80, "bearish_pct": 0.20, "score": 0.75}
        assert apply_sentiment_to_confidence(92, sentiment) == 95

    def test_penalty_when_bearish(self):
        sentiment = {"bullish_pct": 0.30, "bearish_pct": 0.70, "score": 0.25}
        assert apply_sentiment_to_confidence(75, sentiment) == 65

    def test_floor_at_zero(self):
        sentiment = {"bullish_pct": 0.20, "bearish_pct": 0.80, "score": 0.15}
        assert apply_sentiment_to_confidence(5, sentiment) == 0

    def test_neutral_zone_no_change(self):
        sentiment = {"bullish_pct": 0.50, "bearish_pct": 0.50, "score": 0.50}
        assert apply_sentiment_to_confidence(70, sentiment) == 70
