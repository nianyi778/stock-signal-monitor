from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WatchlistItem
from app.schemas import WatchlistItemCreate, WatchlistItemResponse

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/", response_model=list[WatchlistItemResponse])
def list_stocks(db: Session = Depends(get_db)):
    return db.query(WatchlistItem).filter(WatchlistItem.is_active == True).all()  # noqa: E712


@router.post("/", response_model=WatchlistItemResponse, status_code=201)
def add_stock(item: WatchlistItemCreate, db: Session = Depends(get_db)):
    # Check if already exists
    existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == item.ticker.upper()).first()
    if existing:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="Ticker already in watchlist")
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return existing
    # Try to get company name from yfinance (optional, non-blocking)
    name = item.name
    if not name:
        try:
            import yfinance as yf  # noqa: PLC0415

            info = yf.Ticker(item.ticker.upper()).info
            name = info.get("shortName") or info.get("longName") or item.ticker.upper()
        except Exception:
            name = item.ticker.upper()
    db_item = WatchlistItem(ticker=item.ticker.upper(), name=name)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@router.delete("/{ticker}", status_code=200)
def delete_stock(ticker: str, db: Session = Depends(get_db)):
    item = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker.upper()).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ticker not found")
    item.is_active = False
    db.commit()
    return {"status": "deleted", "ticker": ticker.upper()}


@router.post("/scan", status_code=202)
def trigger_scan(background_tasks: BackgroundTasks):
    from app.scheduler import scan_all_stocks_sync  # noqa: PLC0415

    background_tasks.add_task(scan_all_stocks_sync)
    return {"status": "scan_started"}
