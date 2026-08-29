from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db
from .models import AuthToken, ClientApiKey, Device, User


CLIENT_API_SCOPES = frozenset({"translate", "lookup", "download", "account:read"})
DEFAULT_CLIENT_API_SCOPES = ("translate", "lookup", "download", "account:read")
USER_SESSION_COOKIE = "zft_user_session"
ADMIN_SESSION_COOKIE = "zft_admin_session"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def normalize_username(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    clean: list[str] = []
    for raw in scopes or DEFAULT_CLIENT_API_SCOPES:
        value = str(raw or "").strip().lower()
        if value not in CLIENT_API_SCOPES:
            raise ValueError(f"unsupported API key scope: {value}")
        if value not in clean:
            clean.append(value)
    if not clean:
        raise ValueError("at least one API key scope is required")
    return clean


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    salt = os.urandom(16)
    n, r, p = 16384, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n,
        r,
        p,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt_b64, digest_b64 = str(encoded).split("$", 5)
        if kind != "scrypt":
            return False
        pad = lambda s: s + "=" * (-len(s) % 4)
        salt = base64.urlsafe_b64decode(pad(salt_b64))
        expected = base64.urlsafe_b64decode(pad(digest_b64))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, device: Device | None, label: str | None = None) -> tuple[str, AuthToken]:
    raw = "zft_" + secrets.token_urlsafe(48)
    now = utcnow()
    ttl_days = max(1, min(3650, int(get_settings().zft_token_ttl_days or 180)))
    row = AuthToken(
        id=uuid.uuid4().hex,
        user_id=user.id,
        device_id=device.id if device else None,
        token_hash=_token_hash(raw),
        label=(label or None),
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(days=ttl_days),
    )
    db.add(row)
    db.flush()
    return raw, row


def issue_client_api_key(
    db: Session,
    user: User,
    label: str | None = None,
    *,
    scopes: list[str] | tuple[str, ...] | None = None,
    expires_in_days: int | None = None,
    rotated_from_id: str | None = None,
) -> tuple[str, ClientApiKey]:
    """Create a Zotero key; plaintext is returned only from this call."""
    raw = "zftk_" + secrets.token_urlsafe(40)
    now = utcnow()
    clean_scopes = normalize_scopes(scopes)
    expires_at = None
    if expires_in_days is not None:
        days = max(1, min(3650, int(expires_in_days)))
        expires_at = now + timedelta(days=days)
    row = ClientApiKey(
        id=uuid.uuid4().hex,
        user_id=user.id,
        key_hash=_token_hash(raw),
        key_prefix=raw[:16],
        label=(str(label or "").strip()[:120] or None),
        scopes=clean_scopes,
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
        created_at=now,
        last_used_at=None,
        revoked_at=None,
    )
    db.add(row)
    db.flush()
    return raw, row


def get_or_create_device(
    db: Session,
    user: User,
    *,
    device_code: str,
    name: str | None = None,
    platform: str | None = None,
    app_version: str | None = None,
    api_key_id: str | None = None,
) -> Device:
    code = str(device_code or "").strip()
    if len(code) < 4 or len(code) > 160:
        raise HTTPException(status_code=400, detail="invalid device id")
    row = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_code == code))
    now = utcnow()
    fallback_name = f"Client {code[:8]}" if code else "Client"
    clean_name = (str(name or "").strip() or fallback_name)[:180]
    if row is None:
        row = Device(
            id=uuid.uuid4().hex,
            user_id=user.id,
            device_code=code,
            name=clean_name,
            platform=(str(platform).strip()[:80] if platform else None),
            app_version=(str(app_version).strip()[:48] if app_version else None),
            revoked=False,
            created_at=now,
            last_seen_at=now,
            last_api_key_id=api_key_id,
        )
        db.add(row)
        db.flush()
    else:
        if not row.name:
            row.name = clean_name
        if name and str(name).strip():
            row.name = clean_name
        row.platform = (str(platform).strip()[:80] if platform else row.platform)
        row.app_version = (str(app_version).strip()[:48] if app_version else row.app_version)
        row.last_api_key_id = api_key_id or row.last_api_key_id
                                                                                
                                                                                  
        row.last_seen_at = now
    return row


@dataclass(slots=True)
class AuthPrincipal:
    user: User | None = None
    device: Device | None = None
    token: AuthToken | None = None
    api_key: ClientApiKey | None = None
    service: bool = False

    @property
    def authenticated(self) -> bool:
        return self.service or self.user is not None

    @property
    def is_admin(self) -> bool:
        return self.service or bool(self.user and self.user.role == "admin")

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def device_id(self) -> str | None:
        return self.device.id if self.device else None

    @property
    def api_key_id(self) -> str | None:
        return self.api_key.id if self.api_key else None

    @property
    def auth_kind(self) -> str:
        if self.service:
            return "service"
        if self.api_key is not None:
            return "client_api_key"
        if self.token is not None:
            return "web_session"
        return "anonymous"

    @property
    def scopes(self) -> frozenset[str]:
        if self.service:
            return frozenset(CLIENT_API_SCOPES)
        if self.api_key is None:
            return frozenset()
        try:
            return frozenset(normalize_scopes(list(self.api_key.scopes or DEFAULT_CLIENT_API_SCOPES)))
        except ValueError:
            return frozenset()


def _valid_service_key(value: str | None) -> bool:
    expected = get_settings().zft_api_key
    return bool(value and expected and secrets.compare_digest(value, expected))


def _resolve_session_token(db: Session, raw_token: str) -> AuthPrincipal | None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_token)))
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if (_aware(row.expires_at) or now) <= now:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    device = db.get(Device, row.device_id) if row.device_id else None
    if device is not None and device.revoked:
        return None
    row.last_used_at = now
    if device is not None:
        device.last_seen_at = now
    db.commit()
    return AuthPrincipal(user=user, device=device, token=row)


def _resolve_client_api_key(
    db: Session,
    raw_token: str,
    *,
    device_code: str | None = None,
    platform: str | None = None,
    app_version: str | None = None,
) -> AuthPrincipal | None:
    row = db.scalar(select(ClientApiKey).where(ClientApiKey.key_hash == _token_hash(raw_token)))
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if row.expires_at is not None and (_aware(row.expires_at) or now) <= now:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = now
    device = None
    if device_code:
        existing_device = db.scalar(
            select(Device).where(Device.user_id == user.id, Device.device_code == str(device_code).strip())
        )
        if existing_device is not None and existing_device.revoked:
            db.rollback()
            return None
        device = get_or_create_device(
            db,
            user,
            device_code=device_code,
            platform=platform,
            app_version=app_version,
            api_key_id=row.id,
        )
    db.commit()
    return AuthPrincipal(user=user, device=device, api_key=row)


def _resolve_raw_token(
    db: Session,
    raw_token: str | None,
    *,
    device_code: str | None = None,
    platform: str | None = None,
    app_version: str | None = None,
) -> AuthPrincipal | None:
    raw_token = str(raw_token or "").strip()
    if not raw_token:
        return None
    if _valid_service_key(raw_token):
        return AuthPrincipal(service=True)
    if raw_token.startswith("zftk_"):
        return _resolve_client_api_key(db, raw_token, device_code=device_code, platform=platform, app_version=app_version)
    principal = _resolve_session_token(db, raw_token)
    if principal is not None:
        return principal
    return _resolve_client_api_key(db, raw_token, device_code=device_code, platform=platform, app_version=app_version)


def _request_port(request: Request) -> int | None:
    forwarded = request.headers.get("x-forwarded-port")
    if forwarded:
        try:
            return int(forwarded.split(",", 1)[0].strip())
        except Exception:
            pass
    host = request.headers.get("host", "")
    try:
        return int(host.rsplit(":", 1)[1]) if ":" in host else request.url.port
    except Exception:
        return request.url.port


def web_session_cookie_name(request: Request) -> str:
    return ADMIN_SESSION_COOKIE if _request_port(request) == int(get_settings().zft_admin_port) else USER_SESSION_COOKIE


def request_is_secure(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_zft_device_id: str | None = Header(default=None),
    x_zft_platform: str | None = Header(default=None),
    x_zft_client_version: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthPrincipal:
    raw = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            raw = value.strip()
                                                                            
                                                                               
                                                              
    if not raw:
        raw = request.cookies.get(web_session_cookie_name(request))
    principal = _resolve_raw_token(
        db,
        raw,
        device_code=(str(x_zft_device_id or "").strip() or None),
        platform=(str(x_zft_platform or "").strip() or None),
        app_version=(str(x_zft_client_version or "").strip() or None),
    )
    if principal is None and x_api_key:
        principal = _resolve_raw_token(db, x_api_key)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def require_user(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    if principal.user is None:
        raise HTTPException(status_code=403, detail="account authentication required")
    return principal


def require_web_session(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    if principal.user is None or principal.token is None:
        raise HTTPException(status_code=403, detail="web account session required")
    return principal


def require_client_api_key(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    if principal.user is None or principal.api_key is None:
        raise HTTPException(status_code=403, detail="Zotero client API key required")
    return principal


def require_client_scope(scope: str) -> Callable:
    if scope not in CLIENT_API_SCOPES:
        raise ValueError(f"unknown scope: {scope}")

    def dependency(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        if principal.service:
            return principal
        if principal.user is None or principal.api_key is None:
                                                                                  
                                                                                    
            if principal.user is not None and principal.token is not None:
                return principal
            raise HTTPException(status_code=403, detail="Zotero client API key required")
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"API key scope required: {scope}")
        return principal

    return dependency


def require_admin(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    if principal.service:
        return principal
    if not principal.is_admin or principal.token is None:
        raise HTTPException(status_code=403, detail="administrator web session required")
    return principal


def require_api_key(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
    return principal


def require_sse_key(token: str | None = Query(default=None), db: Session = Depends(get_db)) -> AuthPrincipal:
                                                                                 
                                                                                  
    principal = _resolve_raw_token(db, token)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def revoke_token(db: Session, token: AuthToken | None) -> None:
    if token is None or token.revoked_at is not None:
        return
    token.revoked_at = utcnow()
    db.commit()


def revoke_client_api_key(db: Session, key: ClientApiKey | None) -> None:
    if key is None or key.revoked_at is not None:
        return
    key.revoked_at = utcnow()
    db.commit()


def revoke_device_sessions(db: Session, device: Device) -> None:
    device.revoked = True
    rows = db.scalars(select(AuthToken).where(AuthToken.device_id == device.id, AuthToken.revoked_at.is_(None))).all()
    now = utcnow()
    for token in rows:
        token.revoked_at = now
    db.commit()


def ensure_bootstrap_admin() -> None:
    settings = get_settings()
    password = str(settings.zft_bootstrap_admin_password or "")
    if not password:
        return
    with SessionLocal() as db:
        existing_admin = db.scalar(select(User).where(User.role == "admin").limit(1))
        if existing_admin is not None:
            return
        username = normalize_username(settings.zft_bootstrap_admin_username or "admin") or "admin"
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            existing.role = "admin"
            existing.is_active = True
            if not verify_password(password, existing.password_hash):
                existing.password_hash = hash_password(password)
            db.commit()
            return
        db.add(User(
            id=uuid.uuid4().hex,
            username=username,
            display_name="Administrator",
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        ))
        db.commit()
