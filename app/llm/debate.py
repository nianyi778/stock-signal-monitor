"""Bull/Bear signal debate: 3-round LLM review before pushing STRONG signals.

Flow:
  1. Bull analyst  ─┐  (concurrent)
  2. Bear analyst  ─┘
  3. Judge         → PUSH / DOWNGRADE / SUPPRESS

Falls back to PUSH on any error so valid signals are never silently dropped.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI

from app.config import settings
from app.signals.engine import SignalResult

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    return _client


@dataclass
class DebateResult:
    decision: Literal["PUSH", "DOWNGRADE", "SUPPRESS"]
    bull_case: str
    bear_case: str
    verdict: str


async def debate_signal(
    ticker: str,
    signals: list[SignalResult],
    price_context: dict,
    sentiment: dict | None = None,
) -> DebateResult:
    """
    Run 3-round debate (bull → bear → judge) for STRONG signals.
    Falls back to PUSH on any error.

    Args:
        ticker: Stock ticker
        signals: STRONG signal(s) to debate
        price_context: {"current_price", "5d_change_pct", "support", "resistance"}
        sentiment: Optional Finnhub sentiment {"bullish_pct", "bearish_pct", "score"}

    Returns:
        DebateResult with decision PUSH / DOWNGRADE / SUPPRESS
    """
    try:
        ctx = _build_context(ticker, signals, price_context, sentiment)
        bull_case, bear_case = await asyncio.gather(
            _bull_analyst(ctx),
            _bear_analyst(ctx),
        )
        return await _judge(ticker, ctx, bull_case, bear_case)
    except Exception as e:
        logger.warning(f"Debate failed for {ticker}, defaulting to PUSH: {e}")
        return DebateResult(
            decision="PUSH",
            bull_case="(debate error)",
            bear_case="(debate error)",
            verdict="辩论流程异常，保持原始 STRONG 信号。",
        )


def _build_context(
    ticker: str,
    signals: list[SignalResult],
    price_context: dict,
    sentiment: dict | None,
) -> str:
    signal_lines = "\n".join([
        f"- [{s.signal_level}] {s.signal_type} | {s.indicator} | 置信度:{s.confidence}% | {s.message}"
        for s in signals
    ])
    price = price_context.get("current_price", 0)
    change = price_context.get("5d_change_pct", 0)
    support = price_context.get("support")
    resistance = price_context.get("resistance")

    ctx = (
        f"股票: {ticker}\n"
        f"当前价: ${price:.2f}（5日涨跌: {change:+.1f}%）\n"
        f"支撑位: {f'${support:.2f}' if support else '未知'}  "
        f"阻力位: {f'${resistance:.2f}' if resistance else '未知'}\n"
        f"技术信号:\n{signal_lines}"
    )
    if sentiment:
        bull_pct = sentiment.get("bullish_pct", 0)
        bear_pct = sentiment.get("bearish_pct", 0)
        score = sentiment.get("score", 0.5)
        ctx += f"\n新闻情绪: 看多{bull_pct:.0%} / 看空{bear_pct:.0%}（综合分: {score:.2f}）"
    return ctx


async def _bull_analyst(context: str) -> str:
    resp = await _get_client().chat.completions.create(
        model=settings.llm_model_signal,
        messages=[
            {
                "role": "system",
                "content": "你是一位乐观的做多分析师。根据给定信息，给出3条最有力的买入理由。简洁，每条一句话。",
            },
            {"role": "user", "content": context},
        ],
        temperature=0.6,
        max_tokens=200,
    )
    return resp.choices[0].message.content or "无法生成看多论点"


async def _bear_analyst(context: str) -> str:
    resp = await _get_client().chat.completions.create(
        model=settings.llm_model_signal,
        messages=[
            {
                "role": "system",
                "content": "你是一位谨慎的做空分析师。根据给定信息，给出3条最有力的卖出/观望理由。简洁，每条一句话。",
            },
            {"role": "user", "content": context},
        ],
        temperature=0.6,
        max_tokens=200,
    )
    return resp.choices[0].message.content or "无法生成看空论点"


async def _judge(
    ticker: str,
    context: str,
    bull_case: str,
    bear_case: str,
) -> DebateResult:
    prompt = (
        f"你是中立的裁判分析师。综合多空两方论点，给出最终裁决。\n\n"
        f"{context}\n\n"
        f"【多头论点】\n{bull_case}\n\n"
        f"【空头论点】\n{bear_case}\n\n"
        f"请以 JSON 格式输出（不含 markdown code block）：\n"
        f'{{"decision": "PUSH" | "DOWNGRADE" | "SUPPRESS", "verdict": "一句话裁决理由（30字以内）"}}\n\n'
        f"decision 规则：\n"
        f"- PUSH: 多头明显胜出，信号可信，直接推送\n"
        f"- DOWNGRADE: 多空势均力敌，降级为 WEAK 仅记录，不推送\n"
        f"- SUPPRESS: 空头明显胜出或技术信号可疑，丢弃"
    )
    resp = await _get_client().chat.completions.create(
        model=settings.llm_model_signal,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=150,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
        decision = data.get("decision", "PUSH")
        if decision not in ("PUSH", "DOWNGRADE", "SUPPRESS"):
            decision = "PUSH"
        return DebateResult(
            decision=decision,
            bull_case=bull_case,
            bear_case=bear_case,
            verdict=data.get("verdict", ""),
        )
    except (json.JSONDecodeError, KeyError):
        logger.warning(f"Judge JSON parse failed for {ticker}: {raw!r}")
        return DebateResult(
            decision="PUSH",
            bull_case=bull_case,
            bear_case=bear_case,
            verdict=raw[:100],
        )
