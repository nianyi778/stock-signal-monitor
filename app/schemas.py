from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class WatchlistItemCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=5, pattern=r'^[A-Za-z]{1,5}$')
    name: Optional[str] = None


class SignalCreate(BaseModel):
    ticker: str
    signal_type: str  # "BUY" / "SELL" / "WATCH"
    indicator: str
    price: float
    target_price: Optional[float] = None
    message: str
    confidence: int
    signal_level: str  # "STRONG" / "WEAK" / "WATCH"
    user_id: Optional[int] = None


class WatchlistItemResponse(BaseModel):
    id: int
    ticker: str
    name: Optional[str]
    is_active: bool
    created_at: datetime
    user_id: Optional[int]

    model_config = {"from_attributes": True}


class SignalResponse(BaseModel):
    id: int
    ticker: str
    signal_type: str
    indicator: str
    price: float
    target_price: Optional[float]
    message: str
    confidence: int
    signal_level: str
    pushed: bool
    triggered_at: datetime
    user_id: Optional[int]

    model_config = {"from_attributes": True}
