"""Tests for Alpha Vantage news sentiment fetcher."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.news import apply_sentiment_to_confidence, get_ticker_sentiment


class TestGetTickerSentiment:
    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self):
        with patch("app.data.news.settings") as mock_settings:
            mock_settings.alpha_vantage_api_key = ""
            result = await get_ticker_sentiment("AAPL")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_sentiment_dict_on_success(self):
        av_response = {
            "feed": [
                {"ticker_sentiment": [{"ticker": "AAPL", "ticker_sentiment_score": "0.35"}]},
                {"ticker_sentiment": [{"ticker": "AAPL", "ticker_sentiment_score": "0.20"}]},
                {"ticker_sentiment": [{"ticker": "AAPL", "ticker_sentiment_score": "-0.10"}]},
                {"ticker_sentiment": [{"ticker": "AAPL", "ticker_sentiment_score": "0.50"}]},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = av_response

        with patch("app.data.news.settings") as mock_settings, \
             patch("app.data.news.httpx.AsyncClient") as mock_client_cls:
            mock_settings.alpha_vantage_api_key = "test-key"
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await get_ticker_sentiment("AAPL")

        assert result is not None
        # 3 of 4 scores > 0.15 → bullish_pct = 0.75
        assert result["bullish_pct"] == pytest.approx(0.75)
        # 0 scores < -0.15 → bearish_pct = 0.0
        assert result["bearish_pct"] == pytest.approx(0.0)
        # mean(0.35, 0.20, -0.10, 0.50) = 0.2375 → score = (0.2375+1)/2 ≈ 0.619
        assert result["score"] == pytest.approx(0.61875)

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_feed(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"feed": []}

        with patch("app.data.news.settings") as mock_settings, \
             patch("app.data.news.httpx.AsyncClient") as mock_client_cls:
            mock_settings.alpha_vantage_api_key = "test-key"
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await get_ticker_sentiment("AAPL")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        with patch("app.data.news.settings") as mock_settings, \
             patch("app.data.news.httpx.AsyncClient") as mock_client_cls:
            mock_settings.alpha_vantage_api_key = "test-key"
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
