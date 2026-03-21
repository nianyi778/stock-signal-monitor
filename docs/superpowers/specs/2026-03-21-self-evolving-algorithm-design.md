# Self-Evolving Algorithm Design

**Project:** stock-signal-monitor
**Date:** 2026-03-21
**Status:** Approved

---

## Goal

Add a closed-loop learning system to the signal engine. The system tracks the real-world outcome of every pushed STRONG signal, uses external market sentiment to dynamically adjust signal filtering, and automatically re-tunes algorithm parameters each month based on accumulated performance data.

## Architecture Overview

Three phases, each independently deployable, with a clear dependency order:

```
Phase A: Signal Outcome Tracking   (foundation — no deps)
Phase B: External Sentiment Fusion (independent — no deps)
Phase C: Monthly Auto-Tuning       (depends on Phase A data)
```

---

## Data Model

Three new tables added to the existing SQLAlchemy schema. No existing tables are modified.

### SignalOutcome

Stores the result of each pushed STRONG signal, evaluated 5 NYSE trading days after push.

```python
class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"

    id           = Column(Integer, primary_key=True)
    signal_id    = Column(Integer, ForeignKey("signals.id"), nullable=False)
    ticker       = Column(String(10), nullable=False)
    entry_price  = Column(Float, nullable=False)   # price at signal time
    stop_price   = Column(Float, nullable=True)    # stop snapshot from signal
    outcome_price = Column(Float, nullable=True)   # closing price on eval day
    outcome_pct  = Column(Float, nullable=True)    # (outcome - entry) / entry * 100
    result       = Column(String(10), nullable=True)  # WIN / LOSS / NEUTRAL
    evaluated_at = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=lambda: datetime.now(UTC))
```

**Result rules:**
- `WIN`: `outcome_price > entry_price × 1.01` (+1% threshold)
- `LOSS`: `outcome_price ≤ stop_price` (stop was hit)
- `NEUTRAL`: everything else (signal valid, neither target nor stop reached)

**Trading day calculation:** Uses `pandas_market_calendars` with NYSE calendar to count exactly 5 trading days, skipping weekends and US market holidays.

### IndicatorParams

Stores tunable parameters as key/value pairs. Acts as an overlay over `settings.*` defaults — missing keys fall back to `settings` values automatically.

```python
class IndicatorParams(Base):
    __tablename__ = "indicator_params"

    id          = Column(Integer, primary_key=True)
    param_key   = Column(String(64), unique=True, nullable=False)
    param_value = Column(Float, nullable=False)
    updated_at  = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_by  = Column(String(64), default="manual")  # "auto_tune_2026-04" / "manual"
```

**Tunable keys (initial set):**

| Key | Default | Description |
|-----|---------|-------------|
| `push_min_confidence` | 60 | Minimum confidence to push to Telegram |
| `macd_weight` | 1.0 | Multiplier applied to MACD signal confidence |
| `rsi_weight` | 1.0 | Multiplier applied to RSI signal confidence |
| `ma_cross_weight` | 1.0 | Multiplier applied to MA_CROSS signal confidence |
| `volume_ratio_min` | 1.2 | Minimum volume ratio for STRONG confluence |
| `rr_ratio_min` | 1.5 | Minimum R:R ratio to pass signal |

### ParamTuningHistory

Audit log of every auto-tuning event.

```python
class ParamTuningHistory(Base):
    __tablename__ = "param_tuning_history"

    id                = Column(Integer, primary_key=True)
    tuned_at          = Column(DateTime, default=lambda: datetime.now(UTC))
    signals_analyzed  = Column(Integer, nullable=False)
    params_before     = Column(Text, nullable=False)   # JSON snapshot
    params_after      = Column(Text, nullable=False)   # JSON snapshot
    llm_reasoning     = Column(Text, nullable=True)    # LLM explanation
```

---

## Phase A: Signal Outcome Tracker

### File: `app/learning/outcome_tracker.py`

```python
def evaluate_signal_outcomes(db: Session) -> int:
    """
    Find all pushed STRONG signals from exactly 5 NYSE trading days ago
    that have no SignalOutcome record yet. Fetch closing prices and
    record WIN/LOSS/NEUTRAL. Returns count of outcomes evaluated.
    """
```

**Logic:**
1. Compute target evaluation date = today minus 5 NYSE trading days
2. Query `Signal` where `pushed=True`, `signal_level="STRONG"`, `triggered_at::date == target_date`, no existing `SignalOutcome`
3. For each signal: fetch closing price via `yf.Ticker(ticker).fast_info.last_price`
4. Compute `outcome_pct`, determine `result`, write `SignalOutcome`
5. Return count of rows written

**Scheduler integration:** Added to `_daily_job()` after `check_active_trades()`:

```python
def _daily_job():
    scan_all_stocks()
    check_active_trades()
    db = SessionLocal()
    try:
        evaluate_signal_outcomes(db)
    finally:
        db.close()
```

### File: `app/learning/params.py`

```python
def get_param(db: Session, key: str, default: float) -> float:
    """
    Read param from IndicatorParams table.
    Falls back to `default` if key not present.
    Used everywhere settings.* thresholds are referenced.
    """
```

---

## Phase B: External Sentiment Fusion

### File: `app/data/market_sentiment.py`

```python
@dataclass
class MarketSentiment:
    fear_greed_score: int       # 0-100, CNN Fear & Greed
    fear_greed_label: str       # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    vix_30d_slope: float        # linear slope of VIX over last 30 days
    finnhub_bullish_pct: float  # weighted average of watchlist news bullish %
    composite_score: int        # aggregated 0-100 (higher = more bullish)

async def get_market_sentiment(tickers: list[str]) -> MarketSentiment:
    """Fetch all three sentiment signals concurrently. Returns neutral defaults on error."""
```

**Data sources:**

| Source | Endpoint | Caching |
|--------|----------|---------|
| CNN Fear & Greed | `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` (unofficial JSON, no key needed) | 1 hour |
| VIX 30d slope | `yfinance ^VIX` daily close, `tail(30)`, linear regression slope | 1 hour |
| Finnhub news | Existing `get_ticker_sentiment()` per ticker, average across watchlist | 1 hour |

**composite_score formula:**
```
composite = (fear_greed_score * 0.5) + (vix_component * 0.3) + (finnhub_component * 0.2)
```
Where `vix_component = 100 - clip(vix_30d_slope * 200 + 50, 0, 100)` (inverted: rising VIX = lower score).

**Integration in `scheduler.py`** — before Step 1 (news sentiment), fetch market-wide sentiment:

| Condition | Effect |
|-----------|--------|
| `fear_greed_score < 25` (Extreme Fear) | BUY confidence +5, SELL confidence -10 |
| `fear_greed_score > 75` (Extreme Greed) | BUY confidence -5, SELL confidence +5 |
| `vix_30d_slope > 0.3` (fear rising) | `push_min_confidence` += 5 (tighten threshold) |
| `vix_30d_slope < -0.3` (fear falling) | `push_min_confidence` -= 3 (slightly loosen) |

Adjustments are applied in-memory for that scan run only; they do not overwrite `IndicatorParams`.

---

## Phase C: Monthly Auto-Tuning

### File: `app/learning/auto_tuner.py`

```python
def auto_tune_params(db: Session) -> dict | None:
    """
    Analyze last 30 days of SignalOutcome data. Call LLM to recommend
    parameter adjustments. Apply changes within ±20% safety bounds.
    Returns dict of changed params, or None if skipped (< 10 samples).
    """
```

**Algorithm:**
1. Pull `SignalOutcome` records from last 30 days
2. If `count < 10`: log skip reason, return None (insufficient data)
3. Aggregate per-indicator stats:
   ```
   {indicator: {win_rate, avg_win_pct, avg_loss_pct, sample_count}}
   ```
4. Fetch current `IndicatorParams` (or settings defaults)
5. Build LLM prompt with stats + current params
6. Parse LLM JSON response for recommended `param_key → new_value` pairs
7. Apply safety clamp: `new_value = clip(recommended, current * 0.8, current * 1.2)`
8. Write to `IndicatorParams` with `updated_by = f"auto_tune_{year}-{month:02d}"`
9. Write `ParamTuningHistory` record
10. Send Telegram summary

**LLM prompt template:**
```
你是一个量化策略优化师。以下是过去30天的信号表现数据：

{per_indicator_stats_json}

当前参数：
{current_params_json}

请分析表现并给出参数调整建议。规则：
- 每个参数调整幅度不超过当前值的 20%
- 胜率 > 65% 的指标可适当提升权重
- 胜率 < 45% 的指标应降低权重或提升置信度门槛
- 返回纯 JSON，格式：{"param_key": new_value, ...}
- 只返回需要变更的参数，不变的不要包含
```

**Telegram summary format:**
```
📊 本月自动调参完成
分析 {n} 条信号 | 整体胜率 {win_rate:.0%}

变更项：
• macd_weight: 1.00 → 1.15 （胜率 67%，近期表现↑）
• push_min_confidence: 60 → 63 （市场波动上升）

未变更：rsi_weight, ma_cross_weight, rr_ratio_min
```

**Scheduler integration:** New monthly cron job in `start_scheduler()`:
```python
_scheduler.add_job(
    auto_tune_params_job,
    CronTrigger(day=1, hour=8, minute=0, timezone="America/New_York"),
    id="monthly_auto_tune",
    replace_existing=True,
)
```

---

## New MCP Tools

### `stock_monitor_get_signal_stats`

```python
def stock_monitor_get_signal_stats(days: int = 30) -> str:
    """
    Signal performance statistics over the last N days.
    Shows per-indicator win rate, avg P&L, and sample count.
    """
```

Example output:
```
📈 信号表现统计（近30天）

🟢 MACD:      胜率 58% | 均盈 +2.3% | 均亏 -1.8% | 样本 12
🟢 RSI:       胜率 71% | 均盈 +3.1% | 均亏 -1.2% | 样本 7
🟢 MA_CROSS:  胜率 50% | 均盈 +1.8% | 均亏 -2.1% | 样本 4
🟢 MACD+RSI:  胜率 75% | 均盈 +4.2% | 均亏 -1.5% | 样本 8

整体: 31 条信号 | 胜率 63% | 盈亏比 1.8
```

### `stock_monitor_get_tuning_history`

```python
def stock_monitor_get_tuning_history(limit: int = 3) -> str:
    """Recent auto-tuning events with before/after params and LLM reasoning."""
```

---

## File Map

```
app/
├── data/
│   └── market_sentiment.py      NEW — F&G + VIX slope + Finnhub composite
├── learning/                    NEW module
│   ├── __init__.py
│   ├── outcome_tracker.py       NEW — evaluate_signal_outcomes()
│   ├── auto_tuner.py            NEW — auto_tune_params()
│   └── params.py                NEW — get_param() overlay helper
├── models.py                    MODIFY — add 3 new ORM classes
├── scheduler.py                 MODIFY — daily outcome eval + monthly tune + sentiment
├── mcp_server.py                MODIFY — add 2 new tools
└── signals/engine.py            MODIFY — use get_param() for tunable thresholds

tests/
├── test_outcome_tracker.py      NEW
├── test_market_sentiment.py     NEW
└── test_auto_tuner.py           NEW
```

---

## Dependencies

New pip packages required:

| Package | Purpose | Cost |
|---------|---------|------|
| `pandas_market_calendars` | NYSE trading day arithmetic | Free |

No new API keys required. CNN F&G uses a public JSON endpoint.

---

## Implementation Order

1. **Phase A** — Models + outcome tracker + `get_param()` helper + MCP stats tool
2. **Phase B** — Market sentiment module + scheduler integration
3. **Phase C** — Auto-tuner + monthly cron + tuning history MCP tool

Each phase is independently testable and deployable.
