from __future__ import annotations

import base64
import hashlib
import json
from cryptography.fernet import Fernet, InvalidToken
from .config import get_settings


def _fernet() -> Fernet:
    s = get_settings()
    source = (s.zft_config_secret or s.zft_api_key or "change-me").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(source).digest())
    return Fernet(key)


def encrypt_json(value: dict) -> str:
    raw = json.dumps(value or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        raw = _fernet().decrypt(value.encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}
