from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Signal
from app.schemas import SignalResponse

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/", response_model=list[SignalResponse])
def list_signals(
    limit: int = Query(default=50, le=200),
    level: str | None = Query(default=None),  # "STRONG" / "WEAK" / "WATCH"
    db: Session = Depends(get_db),
):
    query = db.query(Signal).order_by(Signal.triggered_at.desc())
    if level:
        query = query.filter(Signal.signal_level == level.upper())
    return query.limit(limit).all()


@router.get("/{ticker}", response_model=list[SignalResponse])
def get_signals_for_ticker(
    ticker: str,
    limit: int = Query(default=20, le=200),
    db: Session = Depends(get_db),
):
    return (
        db.query(Signal)
        .filter(Signal.ticker == ticker.upper())
        .order_by(Signal.triggered_at.desc())
        .limit(limit)
        .all()
    )
