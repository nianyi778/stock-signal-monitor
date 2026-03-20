from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import signals, stocks
from app.bot.application import start_bot, stop_bot
from app.database import Base, engine
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    await start_bot()
    yield
    # Shutdown
    await stop_bot()
    stop_scheduler()


app = FastAPI(title="Stock Signal Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router)
app.include_router(signals.router)


@app.get("/health")
def health():
    return {"status": "ok"}
