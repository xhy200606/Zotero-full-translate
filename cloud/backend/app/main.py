from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .api.account import router as account_router
from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.history import router as history_router
from .api.jobs import router as jobs_router
from .api.providers import router as providers_router
from .api.system import router as system_router
from .auth import ADMIN_SESSION_COOKIE, USER_SESSION_COOKIE, ensure_bootstrap_admin, request_is_secure
from .bootstrap import ensure_runtime_defaults
from .config import get_settings
from .db import Base, engine, ensure_v11_columns, ensure_v22_columns, run_alembic_upgrade
from .services.quota import quota_manager
from .storage import ensure_bucket
from .task_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_v11_columns()
    ensure_v22_columns()
    run_alembic_upgrade()
    ensure_runtime_defaults()
    ensure_bootstrap_admin()
    ensure_bucket()
    manager.start()
    yield
    manager.stop()
    quota_manager.flush_all()


s = get_settings()
app = FastAPI(
    title="Zotero-full-translate Cloud",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if s.zft_expose_api_docs else None,
    redoc_url="/redoc" if s.zft_expose_api_docs else None,
    openapi_url="/openapi.json" if s.zft_expose_api_docs else None,
)

if s.allowed_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.allowed_hosts)

app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-ZFT-Device-ID", "X-ZFT-Platform", "X-ZFT-Client-Version"],
)


def _same_origin(request: Request, origin: str) -> bool:
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return parsed.netloc.lower() == str(request.headers.get("host") or "").strip().lower()


@app.middleware("http")
async def public_security(request: Request, call_next):
    if s.zft_public_hardening and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        has_web_cookie = bool(request.cookies.get(USER_SESSION_COOKIE) or request.cookies.get(ADMIN_SESSION_COOKIE))
        if has_web_cookie:
            origin = str(request.headers.get("origin") or "").strip()
            if origin and not _same_origin(request, origin):
                return JSONResponse({"detail": "cross-site request rejected"}, status_code=403)
    response = await call_next(request)
    if s.zft_public_hardening:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            response.headers.setdefault("Cache-Control", "no-store")
        if request_is_secure(request):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(jobs_router)
app.include_router(providers_router)
app.include_router(system_router)
app.include_router(history_router)


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "zft-cloud",
        "version": __version__,
        "architecture": "single-container",
        "auth": "accounts+web-session+client-api-key",
        "admin_port": s.zft_admin_port,
        "timezone": s.zft_timezone,
        "public_hardening": bool(s.zft_public_hardening),
    }


def _request_port(request: Request) -> int | None:
    forwarded = request.headers.get("x-forwarded-port")
    if forwarded:
        try:
            return int(str(forwarded).split(",", 1)[0].strip())
        except Exception:
            pass
    host = request.headers.get("host", "")
    if host.startswith("[") and "]:" in host:
        try:
            return int(host.rsplit(":", 1)[1])
        except Exception:
            return None
    if ":" in host:
        try:
            return int(host.rsplit(":", 1)[1])
        except Exception:
            return None
    return request.url.port


def _static_root(request: Request) -> Path:
    port = _request_port(request)
    return Path(s.zft_admin_static_dir if port == int(s.zft_admin_port) else s.zft_static_dir)


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(request: Request, full_path: str):
    root = _static_root(request)
    if not root.is_dir():
        return JSONResponse({"detail": "frontend build is not installed"}, status_code=404)
    clean = Path(full_path or "index.html")
    if clean.is_absolute() or ".." in clean.parts:
        return JSONResponse({"detail": "invalid path"}, status_code=400)
    candidate = (root / clean).resolve()
    try:
        candidate.relative_to(root.resolve())
    except Exception:
        return JSONResponse({"detail": "invalid path"}, status_code=400)
    if candidate.is_file():
        # index.html is the entry point that references Vite's content-hashed
        # bundles. Never let a browser/proxy pin an old entry point after an
        # update. Hashed assets may still use normal HTTP caching semantics.
        headers = {"Cache-Control": "no-store, max-age=0"} if candidate.name in {"index.html", "build-id.txt"} else None
        return FileResponse(candidate, headers=headers)
    return FileResponse(root / "index.html", headers={"Cache-Control": "no-store, max-age=0"})
