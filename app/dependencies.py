"""Shared FastAPI dependencies."""

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not settings.api_secret:
        return  # No auth configured, allow all
    if api_key != settings.api_secret:
        raise HTTPException(status_code=401, detail="Invalid API key")
