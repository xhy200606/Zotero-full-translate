from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import (
    ADMIN_SESSION_COOKIE,
    USER_SESSION_COOKIE,
    AuthPrincipal,
    get_or_create_device,
    hash_password,
    issue_token,
    normalize_username,
    request_is_secure,
    require_client_api_key,
    require_web_session,
    revoke_token,
    verify_password,
    web_session_cookie_name,
    utcnow,
)
from ..config import get_settings
from ..db import get_db
from ..models import AuthToken, Device, User
from ..schemas import (
    AuthCapabilities,
    AuthSessionOut,
    BootstrapAdminRequest,
    ClientAuthOut,
    DeviceOut,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    UserPublic,
)
from ..services.usage import record_usage

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
_LOGIN_FAILURES_LOCK = Lock()
_REGISTRATION_LOCK = Lock()
_REGISTRATION_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
_REGISTRATION_ATTEMPTS_LOCK = Lock()


def _login_keys(request: Request, username: str) -> tuple[str, str]:
    host = request.client.host if request.client else "unknown"
    normalized = normalize_username(username)
    return f"ip:{host}", f"user:{normalized}"


def _prune_login_failures(bucket: deque[float], now: float, window: int) -> None:
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _check_login_throttle(request: Request, username: str) -> None:
    settings = get_settings()
    limit = max(3, min(1000, int(settings.zft_login_max_attempts or 10)))
    window = max(30, min(86400, int(settings.zft_login_window_seconds or 300)))
    now = time.monotonic()
    with _LOGIN_FAILURES_LOCK:
        for key in _login_keys(request, username):
            bucket = _LOGIN_FAILURES[key]
            _prune_login_failures(bucket, now, window)
            ceiling = limit * 4 if key.startswith("ip:") else limit
            if len(bucket) >= ceiling:
                retry_after = max(1, int(window - (now - bucket[0])))
                raise HTTPException(
                    status_code=429,
                    detail="too many login attempts; try again later",
                    headers={"Retry-After": str(retry_after)},
                )


def _record_login_failure(request: Request, username: str) -> None:
    settings = get_settings()
    window = max(30, min(86400, int(settings.zft_login_window_seconds or 300)))
    now = time.monotonic()
    with _LOGIN_FAILURES_LOCK:
        for key in _login_keys(request, username):
            bucket = _LOGIN_FAILURES[key]
            _prune_login_failures(bucket, now, window)
            bucket.append(now)


def _clear_login_failures(request: Request, username: str) -> None:
    _, user_key = _login_keys(request, username)
    with _LOGIN_FAILURES_LOCK:
        _LOGIN_FAILURES.pop(user_key, None)



def _check_registration_throttle(request: Request) -> None:
    settings = get_settings()
    limit = max(1, min(1000, int(settings.zft_registration_max_attempts or 5)))
    window = max(60, min(86400, int(settings.zft_registration_window_seconds or 3600)))
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _REGISTRATION_ATTEMPTS_LOCK:
        bucket = _REGISTRATION_ATTEMPTS[host]
        _prune_login_failures(bucket, now, window)
        if len(bucket) >= limit:
            retry_after = max(1, int(window - (now - bucket[0])))
            raise HTTPException(status_code=429, detail="too many registration attempts; try again later", headers={"Retry-After": str(retry_after)})
        bucket.append(now)


def _device_out(device: Device, current: bool = False) -> DeviceOut:
    return DeviceOut.model_validate(device).model_copy(update={"current": current})


def _session_out(token: AuthToken, user: User, device: Device) -> AuthSessionOut:
    return AuthSessionOut(
        expires_at=token.expires_at,
        user=UserPublic.model_validate(user),
        device=_device_out(device, True),
    )


def _retire_browser_device_aliases(db: Session, user: User, current: Device, aliases: list[str] | None) -> None:
    """Retire browser IDs that were replaced by the shared cross-port cookie.

    Before Cloud 2.5, ports 3005 and 3006 each kept their own localStorage
    device UUID. The new frontends send that previous UUID once as an alias
    when adopting the shared host cookie, allowing the server to remove the
    duplicate effective-client entry without guessing from user-agent data.
    """
    clean = []
    for value in aliases or []:
        code = str(value or "").strip()
        if 4 <= len(code) <= 160 and code != current.device_code and code not in clean:
            clean.append(code)
    if not clean:
        return
    old_devices = db.scalars(
        select(Device).where(Device.user_id == user.id, Device.device_code.in_(clean))
    ).all()
    if not old_devices:
        return
    now = utcnow()
    for old in old_devices:
        old.revoked = True
        rows = db.scalars(
            select(AuthToken).where(AuthToken.device_id == old.id, AuthToken.revoked_at.is_(None))
        ).all()
        for token in rows:
            token.revoked_at = now


def _set_session_cookie(response: Response, request: Request, raw: str, token: AuthToken) -> None:
    now = token.created_at
    try:
        max_age = max(1, int((token.expires_at - now).total_seconds()))
    except Exception:
        max_age = max(86400, int(get_settings().zft_token_ttl_days or 180) * 86400)
    response.set_cookie(
        key=web_session_cookie_name(request),
        value=raw,
        max_age=max_age,
        expires=token.expires_at,
        path="/",
        httponly=True,
        secure=request_is_secure(request),
        samesite="lax",
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
                                                                               
                                                             
    for name in {web_session_cookie_name(request), USER_SESSION_COOKIE, ADMIN_SESSION_COOKIE}:
        response.delete_cookie(name, path="/", httponly=True, samesite="lax", secure=request_is_secure(request))


def _authenticate(db: Session, payload: LoginRequest) -> tuple[str, AuthToken, User, Device]:
    username = normalize_username(payload.username)
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="account is disabled")
    device = get_or_create_device(
        db,
        user,
        device_code=payload.device_code,
        name=payload.device_name,
        platform=payload.platform,
        app_version=payload.app_version,
    )
    _retire_browser_device_aliases(db, user, device, payload.device_aliases)
    now = utcnow()
    user.last_login_at = now
    # A browser/device should have a single live web session. Re-authentication
    # rotates the token instead of accumulating parallel sessions for the same
    # client instance. This also makes "有效客户端" reflect actual devices, not
    # repeated logins.
    previous = db.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.device_id == device.id,
            # 3005 (user portal) and 3006 (admin portal) intentionally use
            # different cookies. Keep one live token per portal label so an
            # administrator can stay signed in to both ports on one browser
            # without the two sessions evicting each other.
            AuthToken.label == payload.device_name,
            AuthToken.revoked_at.is_(None),
        )
    ).all()
    for old_token in previous:
        old_token.revoked_at = now
    raw, token = issue_token(db, user, device, label=payload.device_name)
    db.commit()
    record_usage(
        db,
        event_type="login",
        user_id=user.id,
        device_id=device.id,
        details={"platform": payload.platform, "app_version": payload.app_version},
    )
    return raw, token, user, device


@router.get("/capabilities", response_model=AuthCapabilities)
def capabilities(db: Session = Depends(get_db)):
    settings = get_settings()
    user_count = int(db.scalar(select(func.count()).select_from(User)) or 0)
    return AuthCapabilities(
        registration_enabled=True,
        setup_required=user_count == 0,
        first_registered_user_is_admin=True,
        token_ttl_days=max(1, min(3650, int(settings.zft_token_ttl_days or 180))),
    )


@router.post("/login", response_model=AuthSessionOut)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    _check_login_throttle(request, payload.username)
    try:
        raw, token, user, device = _authenticate(db, payload)
        if web_session_cookie_name(request) == ADMIN_SESSION_COOKIE and user.role != "admin":
            revoke_token(db, token)
            raise HTTPException(status_code=403, detail="administrator access required")
    except HTTPException as exc:
        if exc.status_code == 401:
            _record_login_failure(request, payload.username)
        raise
    _clear_login_failures(request, payload.username)
    _set_session_cookie(response, request, raw, token)
    return _session_out(token, user, device)


@router.post("/register", response_model=AuthSessionOut)
def register(payload: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    _check_registration_throttle(request)
    username = normalize_username(payload.username)
    with _REGISTRATION_LOCK:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        user_count = int(db.scalar(select(func.count()).select_from(User)) or 0)
        first_account = user_count == 0
        if web_session_cookie_name(request) == ADMIN_SESSION_COOKIE and not first_account:
            db.rollback()
            raise HTTPException(status_code=403, detail="ordinary users must register from the user portal")
        if db.scalar(select(User).where(User.username == username)) is not None:
            db.rollback()
            raise HTTPException(status_code=409, detail="username already exists")

        user = User(
            id=uuid.uuid4().hex,
            username=username,
            email=(payload.email.strip() if payload.email else None),
            display_name=(payload.display_name.strip() if payload.display_name else username),
            password_hash=hash_password(payload.password),
            role="admin" if first_account else "user",
            is_active=True,
        )
        db.add(user)
        db.flush()
        device = get_or_create_device(
            db,
            user,
            device_code=payload.device_code,
            name=payload.device_name,
            platform=payload.platform,
            app_version=payload.app_version,
        )
        _retire_browser_device_aliases(db, user, device, payload.device_aliases)
        raw, token = issue_token(db, user, device, label=payload.device_name)
        db.commit()

    record_usage(
        db,
        event_type="register",
        user_id=user.id,
        device_id=device.id,
        details={"initial_admin": first_account},
    )
    _set_session_cookie(response, request, raw, token)
    return _session_out(token, user, device)


@router.post("/logout")
def logout(request: Request, response: Response, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    revoke_token(db, principal.token)
    _clear_session_cookies(response, request)
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
def me(principal: AuthPrincipal = Depends(require_web_session)):
    return UserPublic.model_validate(principal.user)


@router.get("/client", response_model=ClientAuthOut)
def client_identity(principal: AuthPrincipal = Depends(require_client_api_key)):
    if "account:read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="API key scope required: account:read")
    return ClientAuthOut(
        user=UserPublic.model_validate(principal.user),
        api_key_id=principal.api_key.id,
        key_prefix=principal.api_key.key_prefix,
        scopes=list(principal.scopes),
        expires_at=principal.api_key.expires_at,
        device=_device_out(principal.device, True) if principal.device else None,
    )


@router.post("/change-password")
def change_password(payload: PasswordChangeRequest, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    user = principal.user
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    rows = db.scalars(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.revoked_at.is_(None))).all()
    from ..auth import utcnow
    now = utcnow()
    for row in rows:
        if principal.token is None or row.id != principal.token.id:
            row.revoked_at = now
    db.commit()
    return {"ok": True}


@router.post("/bootstrap", response_model=UserPublic)
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    import secrets
    if not x_api_key or not settings.zft_api_key or not secrets.compare_digest(x_api_key, settings.zft_api_key):
        raise HTTPException(status_code=401, detail="service API key required")
    if int(db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0) > 0:
        raise HTTPException(status_code=409, detail="administrator already exists")
    username = normalize_username(payload.username)
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    user = User(
        id=uuid.uuid4().hex,
        username=username,
        email=(payload.email.strip() if payload.email else None),
        display_name=(payload.display_name.strip() if payload.display_name else "Administrator"),
        password_hash=hash_password(payload.password),
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return UserPublic.model_validate(user)
