from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
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
        DateTime, default=datetime.utcnow, nullable=False
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
        DateTime, default=datetime.utcnow, nullable=False
    )
    # Reserved for future SaaS multi-tenant expansion
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
