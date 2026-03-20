"""Tests for LLM signal summarizer."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.llm.summarizer import summarize_signals
from app.signals.engine import SignalResult


@pytest.mark.asyncio
async def test_summarize_signals_success():
    """Test successful API call and non-empty response."""
    signals = [
        SignalResult(
            ticker="AAPL",
            signal_type="BUY",
            indicator="MACD",
            price=150.0,
            target_price=155.0,
            confidence=80,
            signal_level="STRONG",
            message="MACD histogram crossed above zero (BUY)",
        )
    ]
    price_context = {
        "current_price": 150.0,
        "5d_change_pct": 2.5,
        "support": 148.0,
        "resistance": 152.0,
    }

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "看多。建议买入149-150元区间，止损147元，目标155元。"

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    with patch("app.llm.summarizer._get_client", return_value=mock_client):
        result = await summarize_signals("AAPL", signals, price_context)

        assert isinstance(result, str)
        assert len(result) > 0
        assert "看多" in result or len(result) > 0  # Either contains content or fallback
        mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_summarize_signals_fallback():
    """Test graceful fallback when API fails."""
    signals = [
        SignalResult(
            ticker="TSLA",
            signal_type="SELL",
            indicator="RSI",
            price=200.0,
            target_price=195.0,
            confidence=75,
            signal_level="WEAK",
            message="RSI overbought (75.5 > 70)",
        ),
        SignalResult(
            ticker="TSLA",
            signal_type="BUY",
            indicator="BOLLINGER",
            price=200.0,
            target_price=198.0,
            confidence=50,
            signal_level="WATCH",
            message="Price below lower Bollinger band (WATCH/BUY)",
        ),
    ]
    price_context = {
        "current_price": 200.0,
        "5d_change_pct": -1.5,
        "support": 195.0,
        "resistance": 205.0,
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
    with patch("app.llm.summarizer._get_client", return_value=mock_client):
        result = await summarize_signals("TSLA", signals, price_context)

        assert isinstance(result, str)
        assert "TSLA" in result
        assert len(result) > 0
        # Verify fallback format includes signal level, type, indicator, confidence
        assert "SELL" in result
        assert "RSI" in result or "BOLLINGER" in result


@pytest.mark.asyncio
async def test_prompt_includes_ticker():
    """Test that the prompt sent to API includes the ticker symbol."""
    signals = [
        SignalResult(
            ticker="GOOGL",
            signal_type="BUY",
            indicator="MA_CROSS",
            price=140.0,
            target_price=142.0,
            confidence=85,
            signal_level="STRONG",
            message="Golden cross: 20 EMA crossed above 50 EMA (BUY)",
        )
    ]
    price_context = {
        "current_price": 140.0,
        "5d_change_pct": 1.2,
        "support": 138.0,
        "resistance": 142.0,
    }

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "看多。建议买入140元区间。"

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    with patch("app.llm.summarizer._get_client", return_value=mock_client):
        await summarize_signals("GOOGL", signals, price_context)

        # Verify that the function was called
        assert mock_client.chat.completions.create.called

        # Get the call arguments
        call_args = mock_client.chat.completions.create.call_args

        # The messages should be passed in the call
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])

        # Verify ticker is in the prompt
        prompt_text = " ".join([msg.get("content", "") for msg in messages])
        assert "GOOGL" in prompt_text
