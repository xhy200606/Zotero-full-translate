from __future__ import annotations

import base64
import hashlib
import json
from cryptography.fernet import Fernet, InvalidToken
from .config import get_settings


_PLACEHOLDER_SECRETS = {
    "change-me",
    "change-me-with-openssl-rand-hex-32",
    "change-me-with-a-different-openssl-rand-hex-32",
}


def _secret_material() -> bytes:
    """Return configured at-rest encryption material or fail closed.

    Provider credentials are persisted in SQLite. A missing/default encryption
    secret must never silently fall back to a public constant because that would
    make a leaked database equivalent to plaintext credentials.
    """
    s = get_settings()
    source = str(s.zft_config_secret or "").strip()
    if not source or source in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "provider-secret encryption is not configured; set a strong ZFT_CONFIG_SECRET"
        )
    return source.encode("utf-8")


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret_material()).digest())
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
    except (InvalidToken, ValueError, json.JSONDecodeError, RuntimeError):
                                                                                  
                                                                               
        return {}
