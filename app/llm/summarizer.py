"""LLM signal summarizer using GPT-4o-mini."""

from openai import AsyncOpenAI
from app.config import settings
from app.signals.engine import SignalResult

_client = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,  # Sub2API proxy
        )
    return _client


async def summarize_signals(
    ticker: str,
    signals: list[SignalResult],
    price_context: dict,  # {"current_price": float, "5d_change_pct": float, "support": float | None, "resistance": float | None}
) -> str:
    """
    Generate LLM analysis summary for signals.
    Falls back to formatted raw signal text if API fails.

    Args:
        ticker: Stock ticker symbol
        signals: List of SignalResult objects
        price_context: Dict with current_price, 5d_change_pct, support, resistance

    Returns:
        LLM-generated summary or formatted fallback string
    """
    # Build signal details string
    signal_details = "\n".join([
        f"- [{signal.signal_level}] {signal.signal_type} | {signal.indicator} | 置信度:{signal.confidence}% | {signal.message}"
        for signal in signals
    ])

    # Format the prompt in Chinese
    prompt = f"""你是一位专业股票分析师。根据以下 {ticker} 的技术指标信号，给出简洁分析：

当前价格: ${price_context['current_price']:.2f}（5日涨跌: {price_context['5d_change_pct']:+.1f}%）
信号详情：
{signal_details}

请提供：
1. 综合判断（看多/看空/观望）
2. 建议操作价位（买入区间/止损位/目标价）
3. 关键理由（2-3句）

简洁扼要，不超过150字。"""

    try:
        response = await _get_client().chat.completions.create(
            model=settings.llm_model_signal,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200,
        )
        return response.choices[0].message.content or _format_fallback(ticker, signals)
    except Exception:
        # Graceful fallback: return formatted raw signal text
        return _format_fallback(ticker, signals)


def _format_fallback(ticker: str, signals: list[SignalResult]) -> str:
    """Format fallback string when API fails."""
    signal_lines = "\n".join([
        f"[{signal.signal_level}] {signal.signal_type} ({signal.indicator}) - 置信度:{signal.confidence}%\n{signal.message}"
        for signal in signals
    ])

    return f"""📊 {ticker} 技术信号摘要
{signal_lines}"""
