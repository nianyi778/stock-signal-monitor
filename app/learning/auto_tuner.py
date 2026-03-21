"""Monthly auto-tuner: analyze SignalOutcome stats, call LLM, update IndicatorParams."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.learning.params import HARD_CEILING, HARD_FLOOR, get_param

logger = logging.getLogger(__name__)

_TUNABLE_KEYS = set(HARD_FLOOR.keys())


def _build_stats(db: Session, days: int = 30) -> dict[str, dict]:
    """Aggregate per-indicator win/loss/neutral stats from last N days."""
    from app.models import SignalOutcome

    since = datetime.now(UTC) - timedelta(days=days)
    outcomes = (
        db.query(SignalOutcome)
        .filter(SignalOutcome.evaluated_at >= since)
        .all()
    )

    stats: dict = defaultdict(lambda: {
        "wins": 0, "losses": 0, "neutrals": 0,
        "win_pcts": [], "loss_pcts": [], "n": 0,
    })
    for o in outcomes:
        key = o.indicator
        stats[key]["n"] += 1
        if o.result == "WIN":
            stats[key]["wins"] += 1
            stats[key]["win_pcts"].append(o.outcome_pct or 0.0)
        elif o.result == "LOSS":
            stats[key]["losses"] += 1
            stats[key]["loss_pcts"].append(o.outcome_pct or 0.0)
        else:
            stats[key]["neutrals"] += 1

    # Convert to serializable summary
    result = {}
    for indicator, s in stats.items():
        n = s["n"]
        if n == 0:
            continue
        win_rate = s["wins"] / n
        avg_win  = sum(s["win_pcts"]) / len(s["win_pcts"]) if s["win_pcts"] else 0.0
        avg_loss = sum(s["loss_pcts"]) / len(s["loss_pcts"]) if s["loss_pcts"] else 0.0
        result[indicator] = {
            "win_rate":    round(win_rate, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct":round(avg_loss, 4),
            "n":           n,
        }
    return result


def _get_current_params(db: Session) -> dict[str, float]:
    """Fetch all tunable params from DB (with defaults)."""
    from app.config import settings
    defaults = {
        "push_min_confidence": float(settings.push_min_confidence),
        "macd_weight":         1.0,
        "rsi_weight":          1.0,
        "ma_cross_weight":     1.0,
        "volume_ratio_min":    1.2,
        "rr_ratio_min":        1.5,
    }
    return {k: get_param(db, k, v) for k, v in defaults.items()}


def _apply_clamp(key: str, current: float, recommended: float) -> float:
    """Apply ±20% relative clamp then absolute hard bounds."""
    clamped = max(current * 0.8, min(current * 1.2, recommended))
    floored  = HARD_FLOOR.get(key, clamped)
    ceilinged = HARD_CEILING.get(key, clamped)
    return round(max(floored, min(ceilinged, clamped)), 4)


def _call_llm(stats_json: str, params_json: str) -> str:
    """Call gpt-4.1 with the tuning prompt. Returns raw string response."""
    from openai import OpenAI
    from app.config import settings

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    prompt = f"""你是一个量化策略优化师。以下是过去30天的信号表现数据：

{stats_json}

每条数据包含：win_rate（胜率）、avg_win_pct（平均盈利%）、avg_loss_pct（平均亏损%，负数）、n（样本数）。

当前参数：
{params_json}

请分析表现并给出参数调整建议。规则：
- 优先看期望值：期望值 = win_rate × avg_win_pct + (1 - win_rate) × avg_loss_pct
  - 期望值 > 0 的指标有正期望，不应降低权重，即使胜率看起来偏低
  - 期望值 < 0 的指标应降低权重，即使胜率看起来还行
- 胜率 > 65% 且期望值 > 0 的指标可适当提升权重（最多 ×1.2）
- 期望值 < -0.5% 的指标应降低权重（最多 ×0.8）
- 样本数 < 5 的指标数据不可信，对应权重保持不变
- 每个参数调整幅度不超过当前值的 20%（系统会自动 clamp）
- 返回纯 JSON，格式：{{"param_key": new_value, ...}}
- 只返回需要变更的参数，不变的不要包含
- 不要包含任何解释文字，只有 JSON"""

    resp = client.chat.completions.create(
        model=settings.llm_model_analysis,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _apply_llm_recommendations(
    db: Session,
    current: dict[str, float],
    recommendations: dict,
) -> dict[str, tuple[float, float]]:
    """
    Validate and apply LLM recommendations.
    Returns {key: (old_value, new_value)} for changed params only.
    """
    from app.models import IndicatorParams

    year = datetime.now(UTC).year
    month = datetime.now(UTC).month
    updated_by = f"auto_tune_{year}-{month:02d}"

    changes = {}
    for key, raw_val in recommendations.items():
        # Skip unknown keys
        if key not in _TUNABLE_KEYS:
            logger.debug(f"Auto-tune: ignoring unknown key '{key}'")
            continue
        # Validate value
        try:
            recommended = float(raw_val)
            if not (recommended > 0 and recommended == recommended):  # positive & not NaN
                raise ValueError
        except (ValueError, TypeError):
            logger.warning(f"Auto-tune: invalid value for '{key}': {raw_val!r}, keeping current")
            continue

        old_val = current.get(key, recommended)
        new_val = _apply_clamp(key, old_val, recommended)
        if abs(new_val - old_val) < 0.0001:
            continue  # no meaningful change

        # Upsert into IndicatorParams
        row = db.query(IndicatorParams).filter_by(param_key=key).first()
        if row:
            row.param_value = new_val
            row.updated_by  = updated_by
        else:
            db.add(IndicatorParams(param_key=key, param_value=new_val, updated_by=updated_by))

        changes[key] = (old_val, new_val)

    db.commit()
    return changes


def auto_tune_params(db: Session) -> Optional[dict]:
    """
    Analyze last 30 days of SignalOutcome data.
    Call gpt-4.1 to recommend parameter adjustments.
    Apply changes within ±20% + absolute hard bounds.

    Returns dict of {key: (old, new)} for changed params, or None if skipped.

    Note: This function runs on the 1st of each month at 08:30 ET.
    The last ~5 trading days of the previous month will NOT be in SignalOutcome yet
    (5-day evaluation lag). Effective lookback window is ~25 trading days.
    """
    from app.models import ParamTuningHistory

    stats = _build_stats(db, days=30)
    total_n = sum(s["n"] for s in stats.values())

    if total_n < 10:
        logger.info(f"Auto-tune skipped: only {total_n} outcomes in last 30 days (need ≥ 10)")
        return None

    current_params = _get_current_params(db)
    stats_json   = json.dumps(stats, indent=2, ensure_ascii=False)
    params_json  = json.dumps(current_params, indent=2)

    raw_response = _call_llm(stats_json, params_json)

    # Parse LLM response
    try:
        # Strip markdown code fences if present
        content = raw_response.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        recommendations = json.loads(content)
        if not isinstance(recommendations, dict):
            raise ValueError("Expected a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Auto-tune: LLM returned unparseable JSON ({e}), skipping")
        return None

    params_before = json.dumps(current_params)
    changes = _apply_llm_recommendations(db, current_params, recommendations)

    if not changes:
        logger.info("Auto-tune: no parameter changes recommended")
        return {}

    # Compute new params snapshot
    new_params = {k: v[1] for k, v in changes.items()}
    for k, v in current_params.items():
        if k not in new_params:
            new_params[k] = v

    # Write audit log
    history = ParamTuningHistory(
        signals_analyzed = total_n,
        params_before    = params_before,
        params_after     = json.dumps(new_params),
        llm_reasoning    = raw_response[:2000],  # truncate for storage
    )
    db.add(history)
    db.commit()

    # Send Telegram summary
    _send_tuning_summary(total_n, stats, changes)
    logger.info(f"Auto-tune complete: {len(changes)} params changed")
    return changes


def _send_tuning_summary(total_n: int, stats: dict, changes: dict) -> None:
    """Send Telegram notification about tuning results (fire and forget)."""
    try:
        from app.notifications.telegram import send_telegram
        import asyncio

        # stats contains summarized data with win_rate and n (not raw wins count)
        total_wins = int(sum(s["win_rate"] * s["n"] for s in stats.values()))
        win_rate = total_wins / total_n if total_n else 0.0

        lines = [f"📊 本月自动调参完成\n分析 {total_n} 条信号 | 整体胜率 {win_rate:.0%}\n"]

        if changes:
            lines.append("变更项：")
            for key, (old_val, new_val) in changes.items():
                direction = "↑" if new_val > old_val else "↓"
                lines.append(f"• {key}: {old_val:.2f} → {new_val:.2f} {direction}")
        else:
            lines.append("本月参数无变更")

        unchanged = [k for k in HARD_FLOOR if k not in changes]
        if unchanged:
            lines.append(f"\n未变更：{', '.join(unchanged)}")

        message = "\n".join(lines)
        asyncio.run(send_telegram(message))
    except Exception as e:
        logger.warning(f"Auto-tune Telegram notification failed: {e}")
