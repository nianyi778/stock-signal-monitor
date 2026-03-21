# Signal Intelligence V2 — Plan 1: 进/跑/割 + 持仓追踪

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 系统给出完整的进场/止盈/止损价格，支持用户录入持仓并实时追踪盈亏，每日自动监控持仓并推送预警。

**Architecture:** 分5个任务：(1) ATR 指标 + 支撑阻力位计算；(2) 信号引擎升级（ATR止损、R:R过滤、成交量/大盘环境前置条件）；(3) 持仓数据模型（ActiveTrade, PositionEntry）；(4) Telegram 持仓管理（录入/查看/卖出）；(5) 日常调度器持仓监控（止损/止盈/预警/财报推送）。

**Tech Stack:** Python 3.12, SQLAlchemy, yfinance, pandas-ta, python-telegram-bot v20

**Spec:** `docs/superpowers/specs/2026-03-21-signal-intelligence-v2-design.md`

---

## File Map

**Modify:**
- `app/signals/indicators.py` — 新增 `calc_atr()`
- `app/signals/engine.py` — 升级 `SignalResult`，新增进场/止损/目标/R:R字段，加前置过滤
- `app/models.py` — 新增 `ActiveTrade`、`PositionEntry` 表
- `app/schemas.py` — 新增对应 Pydantic schema
- `app/scheduler.py` — 新增每日持仓监控逻辑
- `app/bot/handlers.py` — 新增持仓相关 callback handlers
- `app/bot/keyboards.py` — 新增持仓相关键盘按钮
- `app/notifications/telegram.py` — 升级信号推送格式（含进/跑/割）

**Create:**
- `app/bot/portfolio.py` — 持仓追踪业务逻辑（录入/查询/卖出/P&L计算）
- `tests/test_atr.py`
- `tests/test_engine_v2.py`
- `tests/test_portfolio.py`

---

## Task 1: ATR 指标

ATR（Average True Range）用于计算自适应止损位，替代固定 3% 止损。

**Files:**
- Modify: `app/signals/indicators.py`
- Create: `tests/test_atr.py`

- [ ] **1.1 写失败测试**

```python
# tests/test_atr.py
import pandas as pd
import pytest
from app.signals.indicators import calc_atr

def test_atr_returns_series():
    high = pd.Series([105, 107, 106, 108, 110, 112, 111, 113, 115, 114,
                      116, 118, 117, 119, 121, 120, 122, 124, 123, 125])
    low  = pd.Series([100, 102, 101, 103, 105, 107, 106, 108, 110, 109,
                      111, 113, 112, 114, 116, 115, 117, 119, 118, 120])
    close= pd.Series([103, 105, 104, 106, 108, 110, 109, 111, 113, 112,
                      114, 116, 115, 117, 119, 118, 120, 122, 121, 123])
    result = calc_atr(high, low, close, period=14)
    assert isinstance(result, pd.Series)
    assert len(result) == len(close)
    valid = result.dropna()
    assert len(valid) > 0
    assert all(v > 0 for v in valid)

def test_atr_none_on_insufficient_data():
    high = pd.Series([105.0, 107.0])
    low  = pd.Series([100.0, 102.0])
    close= pd.Series([103.0, 105.0])
    result = calc_atr(high, low, close, period=14)
    assert result.dropna().empty
```

- [ ] **1.2 运行测试，确认失败**

```bash
python -m pytest tests/test_atr.py -v
```
预期：ImportError 或 AttributeError

- [ ] **1.3 实现 `calc_atr()`**

在 `app/signals/indicators.py` 末尾追加：

```python
def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    result = ta.atr(high, low, close, length=period)
    if result is None:
        return pd.Series([float("nan")] * len(close), index=close.index)
    return result
```

- [ ] **1.4 运行测试，确认通过**

```bash
python -m pytest tests/test_atr.py -v
```
预期：2 passed

- [ ] **1.5 运行全套测试，确认不回归**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **1.6 提交**

```bash
git add app/signals/indicators.py tests/test_atr.py
git commit -m "feat: add ATR indicator for adaptive stop-loss calculation"
```

---

## Task 2: 信号引擎升级

升级 `SignalResult` 和 `run_signals()`，增加：进场区间、ATR止损、目标价优先级、R:R过滤、成交量过滤、大盘环境过滤。

**Files:**
- Modify: `app/signals/engine.py`
- Create: `tests/test_engine_v2.py`

- [ ] **2.1 写失败测试**

```python
# tests/test_engine_v2.py
"""Tests for upgraded signal engine with entry/stop/target/R:R logic."""
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
import pytest


def _make_df(n=60):
    """Create synthetic OHLCV DataFrame."""
    close = pd.Series([100.0 + i * 0.5 for i in range(n)])
    high  = close + 2
    low   = close - 2
    vol   = pd.Series([1_000_000] * n)
    avg_vol = pd.Series([800_000] * n)  # ratio 1.25 — above 1.2 threshold
    return pd.DataFrame({"Close": close, "High": high, "Low": low,
                         "Volume": vol, "Average_Volume": avg_vol})


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_signal_has_stop_and_target(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    for s in signals:
        if s.signal_level == "STRONG":
            assert s.stop_price is not None, "STRONG signal must have stop_price"
            assert s.target_price is not None, "STRONG signal must have target_price"
            assert s.rr_ratio is not None, "STRONG signal must have rr_ratio"
            assert s.rr_ratio >= 1.5, f"R:R must be >= 1.5, got {s.rr_ratio}"


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_bear_regime_suppresses_buy_signals(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BEAR"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    buy_signals = [s for s in signals if s.signal_type == "BUY" and s.signal_level == "STRONG"]
    assert len(buy_signals) == 0, "No STRONG BUY in BEAR regime"


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_low_volume_suppresses_strong_signals(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 2_000_000  # volume ratio = 0.5x — below 1.2 threshold
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    strong = [s for s in signals if s.signal_level == "STRONG"]
    assert len(strong) == 0, "No STRONG signal on low volume"


@patch("app.signals.engine._get_regime")
@patch("app.signals.engine._get_avg_volume")
@patch("app.signals.engine.fetch_ohlcv")
def test_entry_fields_present(mock_fetch, mock_avgvol, mock_regime):
    mock_fetch.return_value = _make_df()
    mock_avgvol.return_value = 800_000
    mock_regime.return_value = "BULL"

    from app.signals.engine import run_signals
    signals = run_signals("AAPL")
    for s in signals:
        assert hasattr(s, "entry_low")
        assert hasattr(s, "entry_high")
        assert hasattr(s, "stop_price")
        assert hasattr(s, "warn_price")
        assert hasattr(s, "volume_ratio")
        assert hasattr(s, "regime")
```

- [ ] **2.2 运行测试，确认失败**

```bash
python -m pytest tests/test_engine_v2.py -v
```
预期：AttributeError 或 ImportError

- [ ] **2.3 升级 `SignalResult` dataclass**

将 `app/signals/engine.py` 顶部的 `SignalResult` 替换为：

```python
@dataclass
class SignalResult:
    ticker: str
    signal_type: str        # "BUY" / "SELL" / "WATCH"
    indicator: str
    price: float
    target_price: Optional[float]
    confidence: int
    signal_level: str       # "STRONG" / "WEAK" / "WATCH"
    message: str
    # V2 fields — entry/exit/risk
    entry_low: Optional[float] = None    # support + 0.2%
    entry_high: Optional[float] = None   # signal close + 0.5%
    stop_price: Optional[float] = None   # support - 1.5×ATR
    warn_price: Optional[float] = None   # stop + 0.75×ATR
    partial_tp: Optional[float] = None   # target × 95%
    rr_ratio: Optional[float] = None     # (target - mid_entry) / (mid_entry - stop)
    volume_ratio: Optional[float] = None # actual_vol / avg_vol
    regime: Optional[str] = None        # "BULL" / "BEAR" / "NEUTRAL"
    atr: Optional[float] = None
```

- [ ] **2.4 在 `engine.py` 中新增辅助函数**

在 `run_signals()` 上方添加（在现有 import 块下方）：

```python
import yfinance as yf

def _get_regime() -> str:
    """Check SPY vs its 50-day MA and VIX level."""
    try:
        spy = yf.Ticker("SPY")
        info = spy.fast_info
        price = info.get("last_price") or info.get("regularMarketPrice", 0)
        ma50 = info.get("fiftyDayAverage", 0)
        vix = yf.Ticker("^VIX")
        vix_price = vix.fast_info.get("last_price", 0)
        if price > ma50 and vix_price < 25:
            return "BULL"
        elif vix_price >= 25:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"  # default: don't suppress signals on data error


def _get_avg_volume(df: pd.DataFrame) -> float:
    """Return 20-day average volume from OHLCV DataFrame."""
    vol = df.get("Volume")
    if vol is None or len(vol) < 5:
        return 0.0
    return float(vol.tail(20).mean())


def _calc_levels(df: pd.DataFrame, price: float):
    """Return (support, resistance, atr) from recent price data."""
    from app.signals.indicators import calc_atr, calc_bollinger
    close = df["Close"].reset_index(drop=True)
    high  = df["High"].reset_index(drop=True)
    low   = df["Low"].reset_index(drop=True)

    atr_series = calc_atr(high, low, close)
    atr = float(atr_series.dropna().iloc[-1]) if atr_series.dropna().shape[0] > 0 else None

    bb = calc_bollinger(close)
    bb_upper = float(bb["upper"].dropna().iloc[-1]) if bb["upper"].dropna().shape[0] > 0 else None
    bb_lower = float(bb["lower"].dropna().iloc[-1]) if bb["lower"].dropna().shape[0] > 0 else None

    recent_high = float(high.tail(20).max())
    recent_low  = float(low.tail(20).min())

    # Nearest support below price
    candidates_support = [v for v in [bb_lower, recent_low] if v and v < price]
    support = max(candidates_support) if candidates_support else price * 0.97

    # Nearest resistance above price satisfying R:R >= 1.5
    candidates_resist = [v for v in [bb_upper, recent_high] if v and v > price]
    resistance = min(candidates_resist) if candidates_resist else None

    return support, resistance, atr


def _build_entry_exit(price: float, support: float, resistance: Optional[float], atr: Optional[float]):
    """Compute entry range, stop, warn, partial-TP, R:R. Returns None if R:R < 1.5."""
    if atr is None or atr <= 0:
        atr = price * 0.02  # fallback: 2% of price

    entry_low  = round(support * 1.002, 2)
    entry_high = round(price * 1.005, 2)
    stop       = round(support - 1.5 * atr, 2)
    warn       = round(stop + 0.75 * atr, 2)
    mid_entry  = (entry_low + entry_high) / 2

    if resistance is None or resistance <= mid_entry:
        return None  # can't compute valid R:R

    rr = (resistance - mid_entry) / (mid_entry - stop)
    if rr < 1.5:
        return None  # below minimum threshold

    partial_tp = round(resistance * 0.95, 2)
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_price": stop,
        "warn_price": warn,
        "target_price": round(resistance, 2),
        "partial_tp": partial_tp,
        "rr_ratio": round(rr, 2),
    }
```

- [ ] **2.5 在 `run_signals()` 开头添加前置过滤**

在 `run_signals()` 函数中 `df = fetch_ohlcv(ticker)` 之后，`close = ...` 之前插入：

```python
    regime = _get_regime()
    avg_vol = _get_avg_volume(df)
    last_vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
    volume_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0
```

- [ ] **2.6 在 confluence 结果写入时追加 entry/exit 字段**

在 `run_signals()` 中，构建 `strong_buy` 和 `strong_sell` 的部分，改为：

```python
    if len(buy_signals) >= 2:
        # 大盘环境过滤
        if regime == "BEAR":
            pass  # suppress in bear market
        # 成交量过滤
        elif volume_ratio < 1.2:
            pass  # suppress on low volume
        else:
            support, resistance, atr = _calc_levels(df, price)
            ent = _build_entry_exit(price, support, resistance, atr)
            if ent:  # None means R:R < 1.5
                indicator_str = "+".join(s.indicator for s in buy_signals)
                max_conf = max(s.confidence for s in buy_signals)
                confluence_conf = min(95, max_conf + 10 * len(buy_signals))
                final_signals.append(SignalResult(
                    ticker=ticker,
                    signal_type="BUY",
                    indicator=indicator_str,
                    price=price,
                    target_price=ent["target_price"],
                    confidence=confluence_conf,
                    signal_level="STRONG",
                    message=f"Strong BUY: {indicator_str} confluence",
                    entry_low=ent["entry_low"],
                    entry_high=ent["entry_high"],
                    stop_price=ent["stop_price"],
                    warn_price=ent["warn_price"],
                    partial_tp=ent["partial_tp"],
                    rr_ratio=ent["rr_ratio"],
                    volume_ratio=volume_ratio,
                    regime=regime,
                    atr=atr,
                ))

    if len(sell_signals) >= 2:
        support, resistance, atr = _calc_levels(df, price)
        # For SELL: resistance is target, support is stop
        ent = _build_entry_exit(price, support, resistance, atr)
        indicator_str = "+".join(s.indicator for s in sell_signals)
        max_conf = max(s.confidence for s in sell_signals)
        confluence_conf = min(95, max_conf + 10 * len(sell_signals))
        final_signals.append(SignalResult(
            ticker=ticker,
            signal_type="SELL",
            indicator=indicator_str,
            price=price,
            target_price=ent["target_price"] if ent else None,
            confidence=confluence_conf,
            signal_level="STRONG",
            message=f"Strong SELL: {indicator_str} confluence",
            stop_price=ent["stop_price"] if ent else None,
            warn_price=ent["warn_price"] if ent else None,
            rr_ratio=ent["rr_ratio"] if ent else None,
            volume_ratio=volume_ratio,
            regime=regime,
            atr=atr,
        ))
```

Also attach `volume_ratio` and `regime` to WEAK signals:
```python
    # At end, before returning, attach metadata to all signals
    for s in final_signals:
        if s.volume_ratio is None:
            s.volume_ratio = volume_ratio
        if s.regime is None:
            s.regime = regime
```

- [ ] **2.7 运行新测试**

```bash
python -m pytest tests/test_engine_v2.py -v
```
预期：4 passed

- [ ] **2.8 运行全套测试**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **2.9 提交**

```bash
git add app/signals/engine.py tests/test_engine_v2.py
git commit -m "feat: upgrade signal engine with ATR stop, R:R filter, volume/regime gates"
```

---

## Task 3: 持仓数据模型

新增 `ActiveTrade`（每次信号对应的持仓监控记录）和 `PositionEntry`（用户录入的实际买入记录）两张表。

**Files:**
- Modify: `app/models.py`, `app/schemas.py`
- Create: `tests/test_portfolio_models.py`

- [ ] **3.1 写失败测试**

```python
# tests/test_portfolio_models.py
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ActiveTrade, PositionEntry

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_create_active_trade(db):
    trade = ActiveTrade(
        ticker="NVDA",
        signal_id=1,
        entry_low=870.0,
        entry_high=886.0,
        target_price=950.0,
        stop_price=844.0,
        warn_price=851.0,
        partial_tp=902.5,
        rr_ratio=2.1,
        atr_at_signal=12.5,
        volume_ratio=1.4,
        regime_state="BULL",
        status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade)
    db.commit()
    assert trade.id is not None
    assert trade.status == "ACTIVE"

def test_create_position_entry(db):
    entry = PositionEntry(
        ticker="NVDA",
        buy_price=882.5,
        shares=20.0,
        note="第一笔",
    )
    db.add(entry)
    db.commit()
    assert entry.id is not None

def test_position_entry_multiple(db):
    for price, qty in [(200.0, 20), (300.0, 10)]:
        db.add(PositionEntry(ticker="NVDA", buy_price=price, shares=qty))
    db.commit()
    entries = db.query(PositionEntry).filter_by(ticker="NVDA", is_active=True).all()
    assert len(entries) == 2
    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    assert total_shares == 30.0
    assert abs(avg_price - 233.33) < 0.1
```

- [ ] **3.2 运行测试，确认失败**

```bash
python -m pytest tests/test_portfolio_models.py -v
```
预期：ImportError（ActiveTrade, PositionEntry 不存在）

- [ ] **3.3 在 `app/models.py` 追加两个新模型**

```python
class ActiveTrade(Base):
    """Per-signal trade monitoring record. Created when a STRONG signal is pushed."""

    __tablename__ = "active_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK to signals.id

    # Entry window
    entry_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Float, nullable=True)   # invalidation ceiling
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Price targets
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    warn_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Metadata at signal time
    atr_at_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    earnings_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Status: ACTIVE / STOPPED / TARGET_HIT / EXPIRED / CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PositionEntry(Base):
    """User-recorded actual buy entries. Multiple entries per ticker allowed."""

    __tablename__ = "position_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Filled when user records a sell
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
```

- [ ] **3.4 运行测试，确认通过**

```bash
python -m pytest tests/test_portfolio_models.py -v
```
预期：3 passed

- [ ] **3.5 运行全套测试**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **3.6 提交**

```bash
git add app/models.py tests/test_portfolio_models.py
git commit -m "feat: add ActiveTrade and PositionEntry DB models"
```

---

## Task 4: 持仓管理（Telegram）

用户通过 Telegram 录入持仓、查看盈亏、记录卖出。

**Files:**
- Create: `app/bot/portfolio.py`, `tests/test_portfolio.py`
- Modify: `app/bot/keyboards.py`, `app/bot/handlers.py`

- [ ] **4.1 写失败测试**

```python
# tests/test_portfolio.py
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import PositionEntry

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_add_position(db):
    from app.bot.portfolio import add_position, get_positions_summary
    add_position(db, "NVDA", 882.5, 20.0)
    add_position(db, "NVDA", 300.0, 10.0)
    summary = get_positions_summary(db, "NVDA", current_price=910.0)
    assert summary["ticker"] == "NVDA"
    assert summary["total_shares"] == 30.0
    assert abs(summary["avg_price"] - 233.33) < 0.1
    assert summary["current_pnl_pct"] > 0

def test_sell_position(db):
    from app.bot.portfolio import add_position, sell_position
    add_position(db, "NVDA", 100.0, 10.0)
    result = sell_position(db, "NVDA", 120.0)
    assert result["pnl_pct"] == pytest.approx(20.0)
    assert result["pnl_usd"] == pytest.approx(200.0)

def test_get_all_positions(db):
    from app.bot.portfolio import add_position, get_all_positions
    add_position(db, "NVDA", 882.5, 20.0)
    add_position(db, "AAPL", 200.0, 5.0)
    positions = get_all_positions(db)
    tickers = [p["ticker"] for p in positions]
    assert "NVDA" in tickers
    assert "AAPL" in tickers

def test_format_portfolio_message():
    from app.bot.portfolio import format_portfolio_message
    positions = [
        {"ticker": "NVDA", "total_shares": 30.0, "avg_price": 233.33,
         "current_price": 910.0, "current_pnl_pct": 291.0, "current_pnl_usd": 20300.0,
         "position_pct": 27.3},
    ]
    msg = format_portfolio_message(positions, portfolio_value=100_000.0)
    assert "NVDA" in msg
    assert "30" in msg
    assert "233" in msg
```

- [ ] **4.2 运行测试，确认失败**

```bash
python -m pytest tests/test_portfolio.py -v
```
预期：ImportError

- [ ] **4.3 创建 `app/bot/portfolio.py`**

```python
"""Portfolio tracking: position entries, P&L calculation, sell recording."""
from datetime import UTC, datetime
from sqlalchemy.orm import Session
from app.models import PositionEntry


def add_position(db: Session, ticker: str, buy_price: float, shares: float, note: str = "") -> PositionEntry:
    entry = PositionEntry(ticker=ticker.upper(), buy_price=buy_price, shares=shares, note=note)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def sell_position(db: Session, ticker: str, sell_price: float) -> dict:
    """Mark all active entries for ticker as sold. Returns P&L summary."""
    entries = db.query(PositionEntry).filter_by(ticker=ticker.upper(), is_active=True).all()
    if not entries:
        return {"error": f"{ticker} 无持仓记录"}

    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    pnl_usd = (sell_price - avg_price) * total_shares
    pnl_pct = (sell_price - avg_price) / avg_price * 100

    for e in entries:
        e.is_active = False
        e.sell_price = sell_price
        e.sold_at = datetime.now(UTC)
    db.commit()

    return {
        "ticker": ticker,
        "total_shares": total_shares,
        "avg_price": round(avg_price, 2),
        "sell_price": sell_price,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def get_positions_summary(db: Session, ticker: str, current_price: float) -> dict:
    entries = db.query(PositionEntry).filter_by(ticker=ticker.upper(), is_active=True).all()
    if not entries:
        return {"ticker": ticker, "total_shares": 0.0, "avg_price": 0.0,
                "current_pnl_pct": 0.0, "current_pnl_usd": 0.0, "position_pct": 0.0}
    total_shares = sum(e.shares for e in entries)
    avg_price = sum(e.buy_price * e.shares for e in entries) / total_shares
    pnl_usd = (current_price - avg_price) * total_shares
    pnl_pct = (current_price - avg_price) / avg_price * 100
    return {
        "ticker": ticker,
        "total_shares": total_shares,
        "avg_price": round(avg_price, 2),
        "current_price": current_price,
        "current_pnl_pct": round(pnl_pct, 2),
        "current_pnl_usd": round(pnl_usd, 2),
        "position_pct": None,  # filled by caller who knows portfolio_value
    }


def get_all_positions(db: Session) -> list[dict]:
    """Get all active tickers with their aggregate position data."""
    import yfinance as yf
    entries = db.query(PositionEntry).filter_by(is_active=True).all()
    tickers = list({e.ticker for e in entries})
    result = []
    for ticker in tickers:
        try:
            price = yf.Ticker(ticker).fast_info.get("last_price", 0) or 0
        except Exception:
            price = 0
        summary = get_positions_summary(db, ticker, float(price))
        result.append(summary)
    return result


def format_portfolio_message(positions: list[dict], portfolio_value: float = 0) -> str:
    if not positions:
        return "📭 暂无持仓记录。"

    lines = ["*💼 我的持仓*\n"]
    total_pnl = 0.0
    for p in positions:
        ticker = p["ticker"]
        shares = p["total_shares"]
        avg = p["avg_price"]
        curr = p.get("current_price", 0)
        pnl_pct = p["current_pnl_pct"]
        pnl_usd = p["current_pnl_usd"]
        total_pnl += pnl_usd

        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        pct_str = f"{pnl_pct:+.1f}%"
        usd_str = f"${abs(pnl_usd):,.0f}"
        pos_pct = f"{p['position_pct']:.1f}%" if p.get("position_pct") else "—"

        lines.append(
            f"{emoji} *{ticker}*  {shares:.0f}股 @ ${avg:.2f}\n"
            f"  当前 ${curr:.2f}  {pct_str}（{'+' if pnl_usd>=0 else '-'}{usd_str}）  仓位 {pos_pct}"
        )

    if portfolio_value > 0:
        total_pct = total_pnl / portfolio_value * 100
        lines.append(f"\n📊 总浮盈亏: {'🟢' if total_pnl >= 0 else '🔴'} ${total_pnl:+,.0f}（{total_pct:+.1f}%）")

    return "\n".join(lines)
```

- [ ] **4.4 在 `app/bot/keyboards.py` 新增持仓相关按钮**

在文件末尾追加：

```python
PORTFOLIO_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📡 立即扫描", "📋 查看信号"],
        ["📈 我的自选", "➕ 添加股票"],
        ["💼 我的持仓", "📅 大事日历"],
    ],
    resize_keyboard=True,
)

def portfolio_inline(tickers: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(f"📊 {t}", callback_data=f"pos_detail:{t}"),
            InlineKeyboardButton("💰 卖出", callback_data=f"pos_sell:{t}"),
        ]
        for t in tickers
    ]
    buttons.append([InlineKeyboardButton("➕ 录入持仓", callback_data="pos_add")])
    return InlineKeyboardMarkup(buttons)

def confirm_sell_inline(ticker: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认卖出", callback_data=f"pos_sell_confirm:{ticker}"),
        InlineKeyboardButton("❌ 取消", callback_data="cancel"),
    ]])
```

将 `MAIN_KEYBOARD` 替换为 `PORTFOLIO_KEYBOARD`（在 `handlers.py` 中同步更新引用）。

- [ ] **4.5 在 `app/bot/handlers.py` 新增持仓 handler**

在 `callback_handler` 中新增分支（在 `elif data.startswith("analyze:")` 之后）：

```python
    elif data == "pos_add":
        await query.edit_message_text(
            "请输入持仓信息，格式：\n`NVDA 882.5 20`\n（代码 买入均价 股数）",
            parse_mode="Markdown"
        )
        context.user_data["waiting_position"] = True
        return

    elif data.startswith("pos_sell:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效代码")
            return
        context.user_data["selling_ticker"] = ticker
        await query.edit_message_text(
            f"请输入 *{ticker}* 的卖出价格：",
            parse_mode="Markdown"
        )
        context.user_data["waiting_sell_price"] = True
        return

    elif data.startswith("pos_detail:"):
        ticker = data.split(":", 1)[1]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker):
            await query.edit_message_text("❌ 无效代码")
            return
        import yfinance as yf
        from app.bot.portfolio import get_positions_summary
        db = SessionLocal()
        try:
            price = float(yf.Ticker(ticker).fast_info.get("last_price", 0) or 0)
            summary = get_positions_summary(db, ticker, price)
            if summary["total_shares"] == 0:
                await query.edit_message_text(f"📭 {ticker} 无持仓记录")
                return
            pnl_emoji = "🟢" if summary["current_pnl_pct"] >= 0 else "🔴"
            msg = (
                f"*{ticker}* 持仓详情\n\n"
                f"  股数: {summary['total_shares']:.0f}股\n"
                f"  均价: ${summary['avg_price']:.2f}\n"
                f"  现价: ${price:.2f}\n"
                f"  {pnl_emoji} 盈亏: {summary['current_pnl_pct']:+.1f}% "
                f"（${summary['current_pnl_usd']:+,.0f}）"
            )
            await query.edit_message_text(msg, parse_mode="Markdown")
        finally:
            db.close()
```

新增 `btn_portfolio` handler（在 `btn_calendar` 之后）：

```python
@authorized_only
async def btn_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.portfolio import get_all_positions, format_portfolio_message
    from app.config import settings
    db = SessionLocal()
    try:
        positions = get_all_positions(db)
        portfolio_value = float(getattr(settings, "portfolio_value", 0) or 0)
        # Fill position_pct
        total_value = sum(p["current_price"] * p["total_shares"] for p in positions if p.get("current_price"))
        for p in positions:
            if portfolio_value > 0:
                p["position_pct"] = p["current_price"] * p["total_shares"] / portfolio_value * 100
        msg = format_portfolio_message(positions, portfolio_value)
        from app.bot.keyboards import portfolio_inline
        tickers = [p["ticker"] for p in positions if p["total_shares"] > 0]
        markup = portfolio_inline(tickers) if tickers else MAIN_KEYBOARD
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
    finally:
        db.close()
```

在 `build_handlers()` 中新增：

```python
        MessageHandler(filters.Regex("^💼 我的持仓$"), btn_portfolio),
```

- [ ] **4.6 在 `app/config.py` 新增 `portfolio_value` 字段**

```python
portfolio_value: float = 0.0  # Account total value for position sizing, 0 = disabled
```

- [ ] **4.7 在 `.env.example` 追加**

```
PORTFOLIO_VALUE=100000
```

- [ ] **4.8 运行测试**

```bash
python -m pytest tests/test_portfolio.py -v
```
预期：4 passed

- [ ] **4.9 运行全套测试**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **4.10 提交**

```bash
git add app/bot/portfolio.py app/bot/keyboards.py app/bot/handlers.py app/config.py .env.example tests/test_portfolio.py
git commit -m "feat: portfolio tracking — add/view/sell positions with P&L"
```

---

## Task 5: 日常持仓监控调度器

每日收盘后自动检查所有 ACTIVE 持仓，触发条件推送 Telegram。

**Files:**
- Modify: `app/scheduler.py`
- Create: `tests/test_position_monitor.py`

- [ ] **5.1 写失败测试**

```python
# tests/test_position_monitor.py
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ActiveTrade


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@patch("app.scheduler._run_async")
def test_stop_triggered(mock_async, db):
    trade = ActiveTrade(
        ticker="NVDA", entry_low=870.0, entry_high=886.0,
        target_price=950.0, stop_price=844.0, warn_price=851.0,
        partial_tp=902.5, rr_ratio=2.1, status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"NVDA": 840.0})

    db.refresh(trade)
    assert trade.status == "STOPPED"
    assert mock_async.called


@patch("app.scheduler._run_async")
def test_target_triggered(mock_async, db):
    trade = ActiveTrade(
        ticker="AAPL", entry_low=200.0, entry_high=205.0,
        target_price=230.0, stop_price=190.0, warn_price=193.0,
        partial_tp=218.5, rr_ratio=2.0, status="ACTIVE",
        valid_until=datetime.now(UTC) + timedelta(days=3),
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"AAPL": 232.0})

    db.refresh(trade)
    assert trade.status == "TARGET_HIT"


@patch("app.scheduler._run_async")
def test_expiry(mock_async, db):
    trade = ActiveTrade(
        ticker="TSLA", entry_low=200.0, entry_high=210.0,
        target_price=250.0, stop_price=185.0, warn_price=190.0,
        partial_tp=237.5, rr_ratio=2.5, status="ACTIVE",
        valid_until=datetime.now(UTC) - timedelta(days=1),  # already expired
    )
    db.add(trade); db.commit()

    from app.scheduler import check_active_trades
    check_active_trades(db=db, price_override={"TSLA": 220.0})

    db.refresh(trade)
    assert trade.status == "EXPIRED"
```

- [ ] **5.2 运行测试，确认失败**

```bash
python -m pytest tests/test_position_monitor.py -v
```
预期：ImportError（check_active_trades 不存在）

- [ ] **5.3 在 `app/scheduler.py` 新增 `check_active_trades()`**

在 `scan_all_stocks()` 之后追加：

```python
def check_active_trades(db=None, price_override: dict | None = None):
    """Daily check of all ACTIVE trades. Push Telegram on triggers."""
    import yfinance as yf
    from datetime import UTC, datetime
    from app.models import ActiveTrade
    from app.notifications.telegram import send_telegram

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now = datetime.now(UTC)
        trades = db.query(ActiveTrade).filter(ActiveTrade.status == "ACTIVE").all()

        for trade in trades:
            ticker = trade.ticker

            # Get current price
            if price_override and ticker in price_override:
                price = price_override[ticker]
            else:
                try:
                    price = float(yf.Ticker(ticker).fast_info.get("last_price", 0) or 0)
                except Exception:
                    continue
            if price <= 0:
                continue

            # Expiry check
            if trade.valid_until and now > trade.valid_until:
                trade.status = "EXPIRED"
                trade.closed_at = now
                db.commit()
                continue

            # Stop loss
            if trade.stop_price and price <= trade.stop_price:
                trade.status = "STOPPED"
                trade.closed_at = now
                db.commit()
                msg = (
                    f"🔴 *止损提醒* — {ticker}\n"
                    f"  当前价 ${price:.2f} ≤ 止损价 ${trade.stop_price:.2f}\n"
                    f"  建议立即止损，控制损失。"
                )
                _run_async(send_telegram(msg))
                continue

            # Target hit
            if trade.target_price and price >= trade.target_price:
                trade.status = "TARGET_HIT"
                trade.closed_at = now
                db.commit()
                msg = (
                    f"✅ *目标达成* — {ticker}\n"
                    f"  当前价 ${price:.2f} ≥ 目标价 ${trade.target_price:.2f}\n"
                    f"  恭喜！建议考虑止盈。"
                )
                _run_async(send_telegram(msg))
                continue

            # Partial TP
            if trade.partial_tp and price >= trade.partial_tp:
                msg = (
                    f"💰 *分批止盈区间* — {ticker}\n"
                    f"  当前价 ${price:.2f}（目标 95% = ${trade.partial_tp:.2f}）\n"
                    f"  建议卖出 50%，止损上移至保本价 ${(trade.entry_low + trade.entry_high) / 2:.2f}"
                )
                _run_async(send_telegram(msg))

            # Warning
            elif trade.warn_price and price <= trade.warn_price:
                msg = (
                    f"⚠️ *预警* — {ticker}\n"
                    f"  当前价 ${price:.2f} 接近止损位 ${trade.stop_price:.2f}\n"
                    f"  建议密切关注。"
                )
                _run_async(send_telegram(msg))

            # Earnings warning
            if trade.earnings_date:
                days_to_earnings = (trade.earnings_date.date() - now.date()).days
                if days_to_earnings == 7:
                    _run_async(send_telegram(
                        f"📅 *财报预警（7天）* — {ticker}\n"
                        f"  财报日: {trade.earnings_date.date()}，建议缩减至 50% 仓位。"
                    ))
                elif days_to_earnings == 2:
                    _run_async(send_telegram(
                        f"🔴 *财报临近（2天）* — {ticker}\n"
                        f"  建议清仓规避缺口风险！财报日: {trade.earnings_date.date()}"
                    ))
    finally:
        if close_db:
            db.close()
```

- [ ] **5.4 在 `scan_all_stocks()` 后调用**

在 `scheduler.py` 的 `start_scheduler()` 的 job 里，每日扫描后追加：

```python
    def _daily_job():
        scan_all_stocks()
        check_active_trades()
```

将原来的 `scheduler.add_job(scan_all_stocks, ...)` 改为 `scheduler.add_job(_daily_job, ...)`。

- [ ] **5.5 在推送强信号时创建 ActiveTrade 记录**

在 `scan_all_stocks()` 中，找到推送 STRONG 信号后标记 `pushed=True` 的地方，在其后追加：

```python
                    # Create ActiveTrade for monitoring
                    from app.models import ActiveTrade
                    from datetime import timedelta
                    import yfinance as yf
                    try:
                        earnings_dt = None
                        cal = yf.Ticker(ticker).calendar
                        if cal:
                            dates = cal.get("Earnings Date", [])
                            if dates:
                                from datetime import date
                                d = dates[0]
                                if isinstance(d, date):
                                    earnings_dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
                    except Exception:
                        earnings_dt = None

                    for sig in push_signals:
                        if sig.stop_price:  # only for signals with full entry/exit data
                            trade = ActiveTrade(
                                ticker=ticker,
                                signal_id=db_signal.id,
                                entry_low=sig.entry_low,
                                entry_high=sig.entry_high,
                                target_price=sig.target_price,
                                stop_price=sig.stop_price,
                                warn_price=sig.warn_price,
                                partial_tp=sig.partial_tp,
                                rr_ratio=sig.rr_ratio,
                                atr_at_signal=sig.atr,
                                volume_ratio=sig.volume_ratio,
                                regime_state=sig.regime,
                                earnings_date=earnings_dt,
                                status="ACTIVE",
                                valid_until=datetime.now(UTC) + timedelta(days=3),
                            )
                            db.add(trade)
                    db.commit()
```

- [ ] **5.6 运行测试**

```bash
python -m pytest tests/test_position_monitor.py -v
```
预期：3 passed

- [ ] **5.7 运行全套测试**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **5.8 提交**

```bash
git add app/scheduler.py tests/test_position_monitor.py
git commit -m "feat: daily position monitoring with stop/target/warn/earnings push"
```

---

## Task 6: 升级 Telegram 推送格式

让 STRONG 信号推送包含完整的进/跑/割信息。

**Files:**
- Modify: `app/notifications/telegram.py`, `tests/test_telegram.py`

- [ ] **6.1 更新 `format_signal_message()` 函数**

在 `app/notifications/telegram.py` 中，找到 `format_signal_message()` 并替换为：

```python
def format_signal_message(ticker: str, signals: list, summary: str) -> str:
    """Format a STRONG signal push with full entry/exit/risk details."""
    from app.signals.engine import SignalResult

    strong = [s for s in signals if isinstance(s, SignalResult) and s.signal_level == "STRONG"]
    if not strong:
        return ""

    sig = strong[0]
    direction = "🟢 做多" if sig.signal_type == "BUY" else "🔴 做空"
    lines = [
        f"*{ticker}* — {direction}  `{sig.indicator}`",
        "",
    ]

    if sig.entry_low and sig.entry_high:
        lines.append(f"📥 *进场区间:*  ${sig.entry_low:.2f} ~ ${sig.entry_high:.2f}  _（3日内有效）_")
    if sig.target_price:
        rr_str = f"  R:R {sig.rr_ratio:.1f}" if sig.rr_ratio else ""
        lines.append(f"🎯 *目标价:*     ${sig.target_price:.2f}{rr_str}")
    if sig.stop_price:
        lines.append(f"🛑 *止损价:*     ${sig.stop_price:.2f}")
    if sig.partial_tp:
        lines.append(f"💰 *分批止盈:*  ${sig.partial_tp:.2f}  _（卖50%，止损移保本）_")

    lines.append("")
    vol_str = f"📦 成交量: {sig.volume_ratio:.1f}× 均量 ✅" if sig.volume_ratio else ""
    regime_str = f"🌍 大盘: {sig.regime}" if sig.regime else ""
    if vol_str:
        lines.append(vol_str)
    if regime_str:
        lines.append(regime_str)

    if summary:
        lines.extend(["", f"_{summary}_"])

    return "\n".join(lines)
```

- [ ] **6.2 更新 `tests/test_telegram.py` 中的 format 测试**

确保测试验证新字段存在，添加：

```python
def test_format_includes_entry_exit():
    from app.signals.engine import SignalResult
    from app.notifications.telegram import format_signal_message
    sig = SignalResult(
        ticker="NVDA", signal_type="BUY", indicator="MACD+RSI",
        price=882.0, target_price=950.0, confidence=85,
        signal_level="STRONG", message="test",
        entry_low=875.0, entry_high=886.0, stop_price=844.0,
        warn_price=851.0, partial_tp=902.5, rr_ratio=2.1,
        volume_ratio=1.4, regime="BULL",
    )
    msg = format_signal_message("NVDA", [sig], "测试摘要")
    assert "875" in msg
    assert "950" in msg
    assert "844" in msg
    assert "R:R" in msg
```

- [ ] **6.3 运行测试**

```bash
python -m pytest tests/test_telegram.py -v
```
预期：all passed

- [ ] **6.4 运行全套测试**

```bash
python -m pytest tests/ -q
```
预期：all passed

- [ ] **6.5 提交**

```bash
git add app/notifications/telegram.py tests/test_telegram.py
git commit -m "feat: upgrade signal push format with entry/stop/target/R:R"
```

---

## Task 7: 版本号 + 部署

- [ ] **7.1 更新 `docker-compose.yml` 镜像版本**

将 `image: ghcr.io/nianyi778/stock-signal-monitor:1.4.0` 改为 `1.5.0`（两个 service 都改）。

- [ ] **7.2 运行全套测试最终确认**

```bash
python -m pytest tests/ -v
```
预期：all passed

- [ ] **7.3 最终提交 + tag**

```bash
git add docker-compose.yml
git commit -m "chore: bump version to 1.5.0 for signal intelligence V2"
git tag v1.5.0
git push origin main --tags
```

---

## 验证清单

- [ ] `python -m pytest tests/ -v` 全绿
- [ ] 手动在 Telegram 点"📡 立即扫描"，结果包含进场区间/止损/目标价
- [ ] 手动录入持仓 `NVDA 882.5 20`，查看"💼 我的持仓"显示正确均价和盈亏
- [ ] 两笔录入 `NVDA 200 20` + `NVDA 300 10`，验证均价为 $233.33
- [ ] 记录卖出，验证盈亏计算正确
- [ ] GitHub Actions 成功构建并推送 `1.5.0` 镜像
