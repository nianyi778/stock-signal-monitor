"""Tests for app/notifications/telegram.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.signals.engine import SignalResult
from app.notifications.telegram import format_signal_message, send_telegram


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_strong_signal(ticker="AAPL", confidence=85):
    return SignalResult(
        ticker=ticker,
        signal_type="BUY",
        indicator="MACD",
        price=150.0,
        target_price=165.0,
        confidence=confidence,
        signal_level="STRONG",
        message="MACD bullish crossover",
    )


def _sell_signal(ticker="TSLA", confidence=75):
    return SignalResult(
        ticker=ticker,
        signal_type="SELL",
        indicator="RSI",
        price=200.0,
        target_price=None,
        confidence=confidence,
        signal_level="STRONG",
        message="RSI overbought",
    )


# ---------------------------------------------------------------------------
# format_signal_message tests
# ---------------------------------------------------------------------------

class TestFormatSignalMessage:

    def test_format_signal_message_buy(self):
        """BUY STRONG signal → message contains 🟢, ticker, and confidence%."""
        sig = _buy_strong_signal("AAPL", confidence=85)
        msg = format_signal_message("AAPL", [sig], "LLM analysis here")

        assert "🟢" in msg
        assert "AAPL" in msg
        assert "85%" in msg

    def test_format_signal_message_sell(self):
        """SELL signal → message contains 🔴."""
        sig = _sell_signal("TSLA", confidence=75)
        msg = format_signal_message("TSLA", [sig], "LLM sell analysis")

        assert "🔴" in msg
        assert "TSLA" in msg

    def test_format_signal_message_empty_signals(self):
        """Empty signals list → returns 'No signals' text."""
        msg = format_signal_message("NVDA", [], "unused summary")

        assert "No signals" in msg


# ---------------------------------------------------------------------------
# send_telegram tests
# ---------------------------------------------------------------------------

class TestSendTelegram:

    @pytest.mark.asyncio
    async def test_send_telegram_success(self):
        """Mock httpx 200 response → returns True."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.telegram_bot_token = "fake-token"
        mock_settings.telegram_chat_id = "123456"

        with patch("app.notifications.telegram.httpx.AsyncClient", return_value=mock_client), \
             patch("app.notifications.telegram.settings", mock_settings):
            result = await send_telegram("test message")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_telegram_failure(self):
        """Mock httpx 500 response → returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.telegram_bot_token = "fake-token"
        mock_settings.telegram_chat_id = "123456"

        with patch("app.notifications.telegram.httpx.AsyncClient", return_value=mock_client), \
             patch("app.notifications.telegram.settings", mock_settings):
            result = await send_telegram("test message")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_no_token(self):
        """Empty telegram_bot_token → returns False without making any HTTP request."""
        mock_settings = MagicMock()
        mock_settings.telegram_bot_token = ""
        mock_settings.telegram_chat_id = "123456"

        with patch("app.notifications.telegram.httpx.AsyncClient") as mock_client_cls, \
             patch("app.notifications.telegram.settings", mock_settings):
            result = await send_telegram("test message")

        assert result is False
        mock_client_cls.assert_not_called()
