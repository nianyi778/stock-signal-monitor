from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WatchlistItem(Base):
    """Stock tickers to monitor."""

    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    # Reserved for future SaaS multi-tenant expansion
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Signal(Base):
    """Generated trading signals.

    signal_level rules:
      STRONG = ≥2 indicators pointing in the same direction
      WEAK   = single indicator trigger
      WATCH  = near threshold, not yet confirmed
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # signal_type: "BUY" | "SELL" | "WATCH"
    signal_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # indicator: e.g. "MACD+RSI", "RSI", "BOLL"
    indicator: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    # confidence: 0-100 integer score
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    # signal_level: "STRONG" | "WEAK" | "WATCH"
    signal_level: Mapped[str] = mapped_column(String(8), nullable=False)
    # pushed: whether this signal has already been sent via notification
    pushed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    __table_args__ = (
        Index("ix_signals_triggered_at", "triggered_at"),
    )
    # Reserved for future SaaS multi-tenant expansion
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EconomicEvent(Base):
    """Economic calendar events (FOMC, CPI, NFP, earnings, etc.)."""

    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "FOMC" / "CPI" / "NFP" / "EARNINGS" / "GDP" / "PCE"
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str] = mapped_column(String(512), default="", nullable=False, server_default="")
    impact: Mapped[str] = mapped_column(String(8), default="高")  # "高" / "中" / "低"
    source: Mapped[str] = mapped_column(String(32), default="")  # "fed" / "bls" / "finnhub"
    ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)  # for earnings
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class ActiveTrade(Base):
    """Per-signal trade monitoring record. Created when a STRONG signal is pushed."""

    __tablename__ = "active_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    entry_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    warn_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_at_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    earnings_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ACTIVE / STOPPED / TARGET_HIT / EXPIRED / CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionEntry(Base):
    """User-recorded actual buy entries. Multiple entries per ticker allowed."""

    __tablename__ = "position_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class SignalOutcome(Base):
    """Result of a pushed STRONG signal, evaluated 5 NYSE trading days after push."""

    __tablename__ = "signal_outcomes"

    id:            Mapped[int]            = mapped_column(Integer, primary_key=True)
    signal_id:     Mapped[int]            = mapped_column(Integer, ForeignKey("signals.id"), nullable=False, index=True)
    ticker:        Mapped[str]            = mapped_column(String(10), nullable=False)
    indicator:     Mapped[str]            = mapped_column(String(64), nullable=False)
    signal_type:   Mapped[str]            = mapped_column(String(8), nullable=False)
    entry_price:   Mapped[float]          = mapped_column(Float, nullable=False)
    stop_price:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    outcome_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    outcome_pct:   Mapped[float | None]   = mapped_column(Float, nullable=True)
    result:        Mapped[str | None]     = mapped_column(String(10), nullable=True)
    evaluated_at:  Mapped[datetime | None]= mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class IndicatorParams(Base):
    """Tunable parameter overlay. Missing keys fall back to settings.* defaults."""

    __tablename__ = "indicator_params"

    id:          Mapped[int]          = mapped_column(Integer, primary_key=True)
    param_key:   Mapped[str]          = mapped_column(String(64), unique=True, nullable=False)
    param_value: Mapped[float]        = mapped_column(Float, nullable=False)
    updated_at:  Mapped[datetime]     = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by:  Mapped[str]          = mapped_column(String(64), default="manual")


class ParamTuningHistory(Base):
    """Audit log of every auto-tuning event."""

    __tablename__ = "param_tuning_history"

    id:               Mapped[int]      = mapped_column(Integer, primary_key=True)
    tuned_at:         Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    signals_analyzed: Mapped[int]      = mapped_column(Integer, nullable=False)
    params_before:    Mapped[str]      = mapped_column(Text, nullable=False)
    params_after:     Mapped[str]      = mapped_column(Text, nullable=False)
    llm_reasoning:    Mapped[str | None] = mapped_column(Text, nullable=True)
