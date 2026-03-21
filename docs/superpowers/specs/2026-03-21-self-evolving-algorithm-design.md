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
All three new model classes must be imported in `app/models.py` and are automatically
picked up by `Base.metadata.create_all()` which runs at FastAPI startup in `main.py`.

### SignalOutcome

Stores the result of each pushed STRONG signal, evaluated 5 NYSE trading days after push.

```python
class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"

    id            = Column(Integer, primary_key=True)
    signal_id     = Column(Integer, ForeignKey("signals.id"), nullable=False, index=True)
    ticker        = Column(String(10), nullable=False)
    indicator     = Column(String(64), nullable=False)   # denormalized from Signal
    signal_type   = Column(String(8), nullable=False)    # denormalized from Signal
    entry_price   = Column(Float, nullable=False)        # price at signal time
    stop_price    = Column(Float, nullable=True)         # stop snapshot from signal
    outcome_price = Column(Float, nullable=True)         # closing price on eval day (yf.download close)
    outcome_pct   = Column(Float, nullable=True)         # (outcome - entry) / entry * 100
    result        = Column(String(10), nullable=True)    # WIN / LOSS / NEUTRAL
    evaluated_at  = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(UTC))
```

**Result rules (path-dependent):**

Stop evaluation is path-dependent — if the stop price was breached at any point during
the 5-day window, the trade is a LOSS regardless of where price closed on day 5.
A day-5 snapshot alone would misclassify recoveries (e.g., stop hit day 2, price rebounds
by day 5) and inflate apparent signal quality.

```
df_window = yf.download(ticker, start=entry_et_date, end=eval_et_date + 1 day)["Low"]
min_low = df_window.min()
outcome_price = df_window["Close"].iloc[-1]   # day-5 adjusted close for WIN/NEUTRAL

if min_low <= stop_price:
    result = "LOSS"
elif outcome_price > entry_price * 1.01:
    result = "WIN"
else:
    result = "NEUTRAL"
```

- `WIN`: day-5 close > entry × 1.01, AND stop was never breached
- `LOSS`: any-day low ≤ stop_price (path-triggered, not day-5 snapshot)
- `NEUTRAL`: everything else (profitable but < 1%, or slightly below entry but above stop)

**Outcome price source:** `yf.download(ticker, start=..., end=...)` over the full 5-day window
to obtain both the daily low series (for stop check) and the day-5 adjusted close (for WIN/NEUTRAL).
Do NOT use `fast_info.last_price` (real-time tick, not daily close).

**Trading day calculation:** Uses `pandas_market_calendars` with NYSE calendar to count
exactly 5 trading days forward from `triggered_at`, skipping weekends and US holidays.

**UTC/ET date alignment:** `Signal.triggered_at` is stored in UTC. The daily scan runs at
17:00 ET = 22:00 UTC. `evaluate_signal_outcomes()` must convert `triggered_at` to ET before
computing the target evaluation date. `scheduler_cron_hour` must remain ≤ 18 to avoid
the UTC date crossing midnight ahead of the ET date.

**Intraday guard:** Only evaluate signals where the target evaluation date is strictly
before `date.today()` in ET (market is fully closed).

### IndicatorParams

Stores tunable parameters as key/value pairs. Acts as an overlay over `settings.*`
defaults — missing keys fall back to `settings` values automatically.

```python
class IndicatorParams(Base):
    __tablename__ = "indicator_params"

    id          = Column(Integer, primary_key=True)
    param_key   = Column(String(64), unique=True, nullable=False)
    param_value = Column(Float, nullable=False)
    updated_at  = Column(DateTime,
                         default=lambda: datetime.now(UTC),
                         onupdate=lambda: datetime.now(UTC))
    updated_by  = Column(String(64), default="manual")
```

**Tunable keys and absolute bounds:**

| Key | Default | Hard Floor | Hard Ceiling |
|-----|---------|-----------|--------------|
| `push_min_confidence` | 60 | 45 | 85 |
| `macd_weight` | 1.0 | 0.3 | 2.0 |
| `rsi_weight` | 1.0 | 0.3 | 2.0 |
| `ma_cross_weight` | 1.0 | 0.3 | 2.0 |
| `volume_ratio_min` | 1.2 | 1.0 | 2.5 |
| `rr_ratio_min` | 1.5 | 1.0 | 3.0 |

The ±20% relative clamp per cycle is applied first, then the absolute bounds are enforced.
This prevents unbounded drift across multiple tuning cycles (e.g., 6 down-cycles cannot
push `push_min_confidence` below 45 even with the relative clamp).

### ParamTuningHistory

Audit log of every auto-tuning event.

```python
class ParamTuningHistory(Base):
    __tablename__ = "param_tuning_history"

    id               = Column(Integer, primary_key=True)
    tuned_at         = Column(DateTime, default=lambda: datetime.now(UTC))
    signals_analyzed = Column(Integer, nullable=False)
    params_before    = Column(Text, nullable=False)   # JSON snapshot
    params_after     = Column(Text, nullable=False)   # JSON snapshot
    llm_reasoning    = Column(Text, nullable=True)
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

**Algorithm:**
1. Compute `target_date` = today (ET) minus 5 NYSE trading days
2. Guard: if `target_date >= date.today()` in ET, return 0 (market not closed)
3. Query `Signal` where `pushed=True`, `signal_level="STRONG"`,
   `triggered_at` (converted to ET) date == `target_date`,
   no existing `SignalOutcome` record
4. For each signal: `outcome_price = yf.download(ticker, period="1d")["Close"].iloc[-1]`
5. Compute `outcome_pct`, determine `result`, write `SignalOutcome` with denormalized
   `indicator` and `signal_type` copied from the Signal record
6. Return count of rows written

**Scheduler integration:** Added to `_daily_job()` after `check_active_trades()`:

```python
def _daily_job():
    scan_all_stocks()
    check_active_trades()
    db = SessionLocal()
    try:
        from app.learning.outcome_tracker import evaluate_signal_outcomes
        count = evaluate_signal_outcomes(db)
        if count:
            logger.info(f"Evaluated {count} signal outcomes")
    except Exception as e:
        logger.error(f"Signal outcome evaluation error: {e}", exc_info=True)
    finally:
        db.close()
```

### File: `app/learning/params.py`

```python
def get_param(db: Session, key: str, default: float) -> float:
    """
    Read param from IndicatorParams table.
    Falls back to `default` if key not present.
    Used everywhere settings.* thresholds are referenced in engine.py.
    """
```

**Engine integration:** The following hardcoded constants in `engine.py` are migrated to
`get_param()` calls (the function receives a db session passed from the scheduler):

- `volume_ratio >= 1.2` → `get_param(db, "volume_ratio_min", 1.2)`
- `rr < 1.5` → `get_param(db, "rr_ratio_min", 1.5)`

`push_min_confidence` is read in `scheduler.py`, not `engine.py`, so it stays there.

Per-indicator weight multipliers are applied in `engine.py` before confluence:
```python
# After computing base_confidence for each indicator signal:
macd_weight = get_param(db, "macd_weight", 1.0)
sig.confidence = int(min(95, sig.confidence * macd_weight))
```

`run_signals()` signature is extended to accept `db: Session` parameter.

---

## Phase B: External Sentiment Fusion

### File: `app/data/market_sentiment.py`

```python
@dataclass
class MarketSentiment:
    fear_greed_score: int       # 0-100, CNN Fear & Greed
    fear_greed_label: str       # "Extreme Fear"/"Fear"/"Neutral"/"Greed"/"Extreme Greed"
    vix_30d_slope: float        # linear slope of VIX over last 30 daily closes (pts/day)
    finnhub_bullish_pct: float  # 0.0-1.0, average bullish_pct across watchlist tickers
    composite_score: int        # aggregated 0-100 (higher = more bullish market)

async def get_market_sentiment(tickers: list[str]) -> MarketSentiment:
    """Fetch all three sentiment signals concurrently. Returns neutral defaults on error."""
```

**Data sources:**

| Source | Endpoint | Cache TTL |
|--------|----------|-----------|
| CNN Fear & Greed | `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` | 1 hour |
| VIX 30d slope | `yfinance ^VIX` daily close, `tail(30)`, `numpy.polyfit` slope | 1 hour |
| Finnhub news | Existing `get_ticker_sentiment()` per ticker, averaged | 1 hour |

**composite_score formula:**
```
fg_component      = fear_greed_score                       # 0-100, already scaled
vix_component     = clip(100 - (vix_30d_slope * 80 + 50), 0, 100)   # inverted
finnhub_component = finnhub_bullish_pct * 100              # 0.0-1.0 → 0-100
composite_score   = int(fg_component*0.5 + vix_component*0.3 + finnhub_component*0.2)
```

VIX slope multiplier is 80 (not 200) to avoid extreme saturation. A slope of +0.625 pts/day
(VIX rising ~19 pts over 30 days, a severe bear market) maps to vix_component=0.
A slope of -0.625 maps to vix_component=100. Typical range ±0.2 stays well within [20,80].

**Async / sync bridge:** `get_market_sentiment()` is `async`. In `scan_all_stocks()` (sync
scheduler thread), call via the existing `_run_async()` helper:
```python
market_sentiment = _run_async(get_market_sentiment(tickers))
```

**Integration in `scheduler.py`** — before per-ticker loop, once per scan run:

```python
market_sentiment = _run_async(get_market_sentiment(tickers))
```

Per-ticker signal confidence adjustment (applied after `push_signals` is filtered):

| Condition | Effect |
|-----------|--------|
| `fear_greed_score < 25` (Extreme Fear) | BUY `confidence += 5`, SELL `confidence -= 10` |
| `fear_greed_score > 75` (Extreme Greed) | BUY `confidence -= 5`, SELL `confidence += 5` |
| `vix_30d_slope > 0.3` (fear rising) | effective `push_min_confidence += 5` |
| `vix_30d_slope < -0.3` (fear falling) | effective `push_min_confidence -= 3` |

The effective `push_min_confidence` is resolved as:
```python
base = get_param(db, "push_min_confidence", settings.push_min_confidence)
# then Phase B slope adjustment is applied in-memory for this scan run only
```

Adjustments are in-memory only; they do not write back to `IndicatorParams`.

---

## Phase C: Monthly Auto-Tuning

### File: `app/learning/auto_tuner.py`

```python
def auto_tune_params(db: Session) -> dict | None:
    """
    Analyze last 30 days of SignalOutcome data. Call LLM to recommend
    parameter adjustments. Apply changes within ±20% + absolute bounds.
    Returns dict of changed params, or None if skipped (< 10 samples).
    """
```

**Algorithm:**

> **注意：5 交易日评估延迟**
> Phase C 在每月 1 日运行，但上月最后 5 个交易日的信号尚未完成评估（需要再等 5 个交易日才有结果）。
> 这是正常的架构特性，不是 bug。30 天数据窗口中最近 5 天的信号不会出现在 SignalOutcome 表中，
> 因此实际有效样本约为 25 天。`count < 10` 的最低门槛已考虑此 lag，无需额外处理。

1. Pull `SignalOutcome` records from last 30 days
2. If `count < 10`: log reason, return None (insufficient data)
3. Aggregate per-indicator stats:
   ```python
   {indicator: {"win_rate": float, "avg_win_pct": float, "avg_loss_pct": float, "n": int}}
   ```
4. Fetch current resolved params via `get_param()` for all tunable keys
5. Build LLM prompt (use `settings.llm_model_analysis` = `gpt-4.1`, not the cheaper mini)
6. Call LLM, parse response — validation steps:
   - Must be valid JSON; on parse error → skip tuning, log warning
   - Each key must be in the known tunable key set; unknown keys → ignored
   - Each value must be a positive finite float; invalid values → keep current
7. Apply safety clamp per param:
   ```python
   clamped = clip(recommended, current * 0.8, current * 1.2)
   final   = clip(clamped, HARD_FLOOR[key], HARD_CEILING[key])
   ```
8. Write changed params to `IndicatorParams` with `updated_by = f"auto_tune_{year}-{month:02d}"`
9. Write `ParamTuningHistory` record (params_before and params_after as JSON)
10. Send Telegram summary (see format below)

**Scheduler wrapper** (defined in `scheduler.py`):
```python
def auto_tune_params_job() -> None:
    """Wrapper for monthly auto-tune cron job."""
    db = SessionLocal()
    try:
        from app.learning.auto_tuner import auto_tune_params
        result = auto_tune_params(db)
        if result is None:
            logger.info("Auto-tune skipped: insufficient signal outcomes")
    except Exception as e:
        logger.error(f"Auto-tune error: {e}", exc_info=True)
    finally:
        db.close()
```

**Cron schedule:** 1st of each month, 08:30 ET (offset from 08:00 calendar refresh to
avoid simultaneous Telegram messages):
```python
_scheduler.add_job(
    auto_tune_params_job,
    CronTrigger(day=1, hour=8, minute=30, timezone="America/New_York"),
    id="monthly_auto_tune",
    replace_existing=True,
)
```

**LLM prompt template:**
```
你是一个量化策略优化师。以下是过去30天的信号表现数据：

{per_indicator_stats_json}

每条数据包含：win_rate（胜率）、avg_win_pct（平均盈利%）、avg_loss_pct（平均亏损%，负数）、n（样本数）。

当前参数：
{current_params_json}

请分析表现并给出参数调整建议。规则：
- 优先看期望值：期望值 = win_rate × avg_win_pct + (1 - win_rate) × avg_loss_pct
  - 期望值 > 0 的指标有正期望，不应降低权重，即使胜率看起来偏低（如 40% 胜率但盈亏比 3:1）
  - 期望值 < 0 的指标应降低权重，即使胜率看起来还行
- 胜率 > 65% 且期望值 > 0 的指标可适当提升权重（最多 ×1.2）
- 期望值 < -0.5% 的指标应降低权重（最多 ×0.8）
- 样本数 < 5 的指标数据不可信，对应权重保持不变
- 每个参数调整幅度不超过当前值的 20%（系统会自动 clamp）
- 返回纯 JSON，格式：{"param_key": new_value, ...}
- 只返回需要变更的参数，不变的不要包含
- 不要包含任何解释文字，只有 JSON
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

---

## New MCP Tools

### `stock_monitor_get_signal_stats`

```python
def stock_monitor_get_signal_stats(days: int = 30) -> str:
    """
    Signal performance statistics over the last N days.
    Shows per-indicator win rate, avg P&L, and sample count.
    Uses SignalOutcome table. Returns "no data" message if < 5 outcomes exist.
    """
```

Pattern: sync, opens own DB session, follows existing MCP tool conventions.

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

Pattern: sync, opens own DB session.

---

## File Map

```
app/
├── data/
│   └── market_sentiment.py      NEW — F&G + VIX slope + Finnhub composite
├── learning/                    NEW module
│   ├── __init__.py
│   ├── outcome_tracker.py       NEW — evaluate_signal_outcomes()
│   ├── auto_tuner.py            NEW — auto_tune_params(), HARD_FLOOR/CEILING constants
│   └── params.py                NEW — get_param() overlay helper
├── models.py                    MODIFY — add SignalOutcome, IndicatorParams, ParamTuningHistory
│                                         add index=True on Signal.triggered_at
├── scheduler.py                 MODIFY — _daily_job() + start_scheduler() + auto_tune_params_job()
│                                         + market_sentiment fetch before per-ticker loop
├── mcp_server.py                MODIFY — add stock_monitor_get_signal_stats + get_tuning_history
└── signals/engine.py            MODIFY — run_signals(ticker, db) signature
                                          get_param() for volume_ratio_min, rr_ratio_min, weights

tests/
├── test_outcome_tracker.py      NEW — 5-trading-day calc, WIN/LOSS/NEUTRAL, holiday handling
├── test_market_sentiment.py     NEW — composite_score formula, async fetch, cache
└── test_auto_tuner.py           NEW — clamp logic, LLM parse validation, skip < 10 samples
```

---

## Dependencies

| Package | Purpose | Cost |
|---------|---------|------|
| `pandas_market_calendars` | NYSE trading day arithmetic | Free |

No new API keys required. CNN F&G uses a public JSON endpoint.

---

## Implementation Order

1. **Phase A** — Models + `Signal.triggered_at` index + outcome tracker + `get_param()` + engine migration + MCP stats tool
2. **Phase B** — Market sentiment module + scheduler integration
3. **Phase C** — Auto-tuner + monthly cron wrapper + tuning history MCP tool
