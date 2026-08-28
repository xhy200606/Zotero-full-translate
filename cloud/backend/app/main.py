from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.jobs import router as jobs_router
from .api.providers import router as providers_router
from .api.system import router as system_router
from .api.history import router as history_router
from .bootstrap import ensure_runtime_defaults
from .config import get_settings
from .db import Base, engine, ensure_v11_columns
from .storage import ensure_bucket
from .task_manager import manager
from .services.history import backfill_source_hashes
from .services.quota import quota_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.work_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_v11_columns()
    ensure_runtime_defaults()
    ensure_bucket()
    backfill_source_hashes()
    manager.start()
    yield
    manager.stop()
    quota_manager.flush_all()


app = FastAPI(title="Zotero-full-translate Cloud", version=__version__, lifespan=lifespan)
s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(jobs_router)
app.include_router(providers_router)
app.include_router(system_router)
app.include_router(history_router)


@app.get("/health")
def health():
    return {"ok": True, "service": "zft-cloud", "version": __version__, "architecture": "single-container"}


static_dir = Path(s.zft_static_dir)
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
