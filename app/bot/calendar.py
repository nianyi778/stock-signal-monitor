"""US economic calendar via LLM."""
import logging
from datetime import date

from app.config import settings
from app.llm.summarizer import _get_client

logger = logging.getLogger(__name__)


async def get_upcoming_events(days: int = 14) -> str:
    """Ask LLM for upcoming economic events."""
    today = date.today()
    prompt = f"""今天是 {today}。请列出未来 {days} 天内的美股重大经济事件和数据发布。

包括但不限于：
- FOMC 利率决议
- CPI / PPI 消费者/生产者物价指数
- 非农就业数据
- GDP 数据
- PCE 物价指数
- 失业率
- 零售销售数据
- 美联储官员重要讲话
- 重大科技股财报（AAPL/NVDA/MSFT/GOOGL/META/AMZN/TSLA 等）

格式要求：
📅 *美股大事日历* (未来{days}天)

每条用这个格式：
⚡ MM/DD (周X) | 事件名称 | 影响程度(高/中/低) | 简述预期

如果没有重大事件就说明。请用中文回答，简洁准确。"""

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=settings.llm_model_analysis,
            messages=[
                {"role": "system", "content": "你是一位专业的美股宏观分析师，熟悉美国经济数据发布日历和美联储政策日程。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Calendar LLM error: {e}")
        return "❌ 获取经济日历失败，请稍后重试。"
