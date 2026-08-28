import secrets
from fastapi import Header, HTTPException, Query
from .config import get_settings


def _valid(value: str | None) -> bool:
    expected = get_settings().zft_api_key
    return bool(value and expected and secrets.compare_digest(value, expected))


def require_api_key(x_api_key: str | None = Header(default=None)):
    if not _valid(x_api_key):
        raise HTTPException(status_code=401, detail="invalid API key")


def require_sse_key(token: str | None = Query(default=None)):
    if not _valid(token):
        raise HTTPException(status_code=401, detail="invalid API key")
