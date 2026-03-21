"""Tests for Bull/Bear debate module."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.debate import DebateResult, _build_context, debate_signal
from app.signals.engine import SignalResult


def _make_signal(
    ticker="AAPL",
    signal_type="BUY",
    confidence=80,
    signal_level="STRONG",
    indicator="MACD+RSI",
) -> SignalResult:
    return SignalResult(
        ticker=ticker,
        signal_type=signal_type,
        indicator=indicator,
        price=150.0,
        target_price=165.0,
        stop_price=142.0,
        warn_price=144.0,
        entry_low=148.0,
        entry_high=152.0,
        partial_tp=156.75,
        rr_ratio=2.0,
        atr=4.5,
        confidence=confidence,
        signal_level=signal_level,
        message="MACD histogram crossed zero, RSI recovering from oversold",
        volume_ratio=1.3,
        regime="BULL",
    )


def _make_price_context():
    return {
        "current_price": 150.0,
        "5d_change_pct": 2.5,
        "support": 145.0,
        "resistance": 168.0,
    }


class TestBuildContext:
    def test_basic_context_no_sentiment(self):
        sig = _make_signal()
        ctx = _build_context("AAPL", [sig], _make_price_context(), None)
        assert "AAPL" in ctx
        assert "$150.00" in ctx
        assert "MACD+RSI" in ctx
        assert "新闻情绪" not in ctx

    def test_context_with_sentiment(self):
        sig = _make_signal()
        sentiment = {"bullish_pct": 0.7, "bearish_pct": 0.3, "score": 0.65}
        ctx = _build_context("AAPL", [sig], _make_price_context(), sentiment)
        assert "新闻情绪" in ctx
        assert "70%" in ctx


class TestDebateSignal:
    def _make_judge_response(self, decision: str, verdict: str) -> str:
        return json.dumps({"decision": decision, "verdict": verdict})

    @pytest.mark.asyncio
    async def test_push_decision(self):
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client

            def _resp(content):
                r = MagicMock()
                r.choices[0].message.content = content
                return r

            mock_client.chat.completions.create = AsyncMock(side_effect=[
                _resp("1. MACD crossed zero. 2. RSI recovering. 3. Volume confirms."),
                _resp("1. Market uncertain. 2. Resistance nearby. 3. Sector weak."),
                _resp(self._make_judge_response("PUSH", "多头信号明确，技术面支持买入")),
            ])

            result = await debate_signal("AAPL", [_make_signal()], _make_price_context())
            assert result.decision == "PUSH"
            assert result.verdict == "多头信号明确，技术面支持买入"
            assert "MACD" in result.bull_case

    @pytest.mark.asyncio
    async def test_downgrade_decision(self):
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client

            def _resp(content):
                r = MagicMock()
                r.choices[0].message.content = content
                return r

            mock_client.chat.completions.create = AsyncMock(side_effect=[
                _resp("看多理由"),
                _resp("看空理由"),
                _resp(self._make_judge_response("DOWNGRADE", "多空势均力敌，信号存疑")),
            ])

            result = await debate_signal("AAPL", [_make_signal()], _make_price_context())
            assert result.decision == "DOWNGRADE"

    @pytest.mark.asyncio
    async def test_suppress_decision(self):
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client

            def _resp(content):
                r = MagicMock()
                r.choices[0].message.content = content
                return r

            mock_client.chat.completions.create = AsyncMock(side_effect=[
                _resp("看多理由"),
                _resp("看空理由"),
                _resp(self._make_judge_response("SUPPRESS", "空头占优，信号不可信")),
            ])

            result = await debate_signal("AAPL", [_make_signal()], _make_price_context())
            assert result.decision == "SUPPRESS"

    @pytest.mark.asyncio
    async def test_fallback_to_push_on_error(self):
        """Any exception → PUSH fallback, never suppress valid signals silently."""
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client_fn.side_effect = RuntimeError("network error")
            result = await debate_signal("AAPL", [_make_signal()], _make_price_context())
            assert result.decision == "PUSH"

    @pytest.mark.asyncio
    async def test_invalid_judge_json_falls_back_to_push(self):
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client

            def _resp(content):
                r = MagicMock()
                r.choices[0].message.content = content
                return r

            mock_client.chat.completions.create = AsyncMock(side_effect=[
                _resp("bull"),
                _resp("bear"),
                _resp("not json at all"),
            ])

            result = await debate_signal("AAPL", [_make_signal()], _make_price_context())
            assert result.decision == "PUSH"

    @pytest.mark.asyncio
    async def test_sentiment_included_in_context(self):
        """Sentiment data is passed to the debate context."""
        with patch("app.llm.debate._get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            captured_prompts = []

            async def capture_call(**kwargs):
                msg = kwargs["messages"][-1]["content"]
                captured_prompts.append(msg)
                r = MagicMock()
                if len(captured_prompts) == 3:
                    r.choices[0].message.content = json.dumps(
                        {"decision": "PUSH", "verdict": "ok"}
                    )
                else:
                    r.choices[0].message.content = "reason"
                return r

            mock_client.chat.completions.create = AsyncMock(side_effect=capture_call)
            sentiment = {"bullish_pct": 0.75, "bearish_pct": 0.25, "score": 0.7}
            await debate_signal("AAPL", [_make_signal()], _make_price_context(), sentiment)

            # sentiment should appear in at least the first call context
            assert any("新闻情绪" in p for p in captured_prompts)
