"""Parameter overlay: reads tunable params from DB, falls back to defaults."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session


# Absolute hard bounds — enforced after ±20% relative clamp in auto_tuner.
# Prevents unbounded drift across multiple tuning cycles.
HARD_FLOOR: dict[str, float] = {
    "push_min_confidence": 45.0,
    "macd_weight":         0.3,
    "rsi_weight":          0.3,
    "ma_cross_weight":     0.3,
    "volume_ratio_min":    1.0,
    "rr_ratio_min":        1.0,
}

HARD_CEILING: dict[str, float] = {
    "push_min_confidence": 85.0,
    "macd_weight":         2.0,
    "rsi_weight":          2.0,
    "ma_cross_weight":     2.0,
    "volume_ratio_min":    2.5,
    "rr_ratio_min":        3.0,
}


def get_param(db: Optional[Session], key: str, default: float) -> float:
    """
    Read a tunable parameter from IndicatorParams table.

    Falls back to `default` when:
    - db is None (e.g., called from bot/analysis.py without a db session)
    - key not present in IndicatorParams table

    Args:
        db: SQLAlchemy session, or None for fallback-only mode.
        key: Parameter key, e.g. "volume_ratio_min".
        default: Value to return when key is absent.

    Returns:
        Float value from DB or default.
    """
    if db is None:
        return default
    try:
        from app.models import IndicatorParams
        row = db.query(IndicatorParams).filter_by(param_key=key).first()
        if row is not None:
            return float(row.param_value)
    except Exception:
        pass
    return default
