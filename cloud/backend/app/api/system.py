from __future__ import annotations

import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..auth import require_api_key
from ..config import get_settings
from ..db import get_db
from ..models import Job, ProviderProfile, RuntimeConfig
from ..schemas import RuntimeOut, RuntimeUpdate, SystemStatus, WorkerOut
from ..services.rate_limit import gate
from ..services.providers import provider_is_configured
from ..storage import ensure_bucket
from ..task_manager import manager

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _runtime(db: Session) -> RuntimeConfig:
    row = db.get(RuntimeConfig, 1)
    if row is None:
        raise HTTPException(503, "runtime configuration is not initialized")
    return row


def _providers_with_metrics(db: Session, qps: int) -> list[dict]:
    rows = db.query(ProviderProfile).order_by(ProviderProfile.id.asc()).all()
    result = []
    for x in rows:
        try: provider_qps = float((x.config or {}).get("qps") or qps)
        except Exception: provider_qps = float(qps)
        result.append(gate.snapshot(x.id, provider_qps) | {"enabled": x.enabled, "display_name": x.display_name})
    return result


@router.get("/status", response_model=SystemStatus, dependencies=[Depends(require_api_key)])
def system_status(db: Session = Depends(get_db)):
    runtime = _runtime(db)
    db_ok = storage_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    try:
        ensure_bucket()
        storage_ok = get_settings().storage_dir.is_dir()
    except Exception:
        pass

    def count(statuses):
        return int(db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(statuses))) or 0)

    snap = manager.snapshot()
    active = count(["PARSING", "TRANSLATING", "TYPESETTING", "RENDERING", "FINALIZING", "CANCELLING", "RUNNING"])
    queued = count(["CREATED", "UPLOADING", "QUEUED"])
    completed = count(["COMPLETED"])
    failed = count(["FAILED"])
    return SystemStatus(
        ok=db_ok and storage_ok,
        version=__version__,
        database=db_ok,
        redis=True,  # compatibility field: embedded queue is healthy while the process is alive
        storage=storage_ok,
        translator_provider=runtime.default_provider,
        queue_depth=snap["queued"],
        active_jobs=active,
        queued_jobs=queued,
        completed_jobs=completed,
        failed_jobs=failed,
        server_limits={
            "architecture": "single-container",
            "database": "sqlite",
            "queue": "embedded",
            "storage": "local-volume",
            "max_active_jobs": runtime.max_active_jobs,
            "default_provider_ids": list(getattr(runtime, "default_provider_ids", None) or [runtime.default_provider]),
            "default_provider_strategy": str(getattr(runtime, "default_provider_strategy", None) or "balanced"),
            "babeldoc_qps": runtime.babeldoc_qps,
            "pool_max_workers": runtime.pool_max_workers,
            "multi_pool_max_workers": getattr(runtime, "multi_pool_max_workers", 12),
            "aggregate_qps_cap": getattr(runtime, "aggregate_qps_cap", 100),
            "max_pages_per_part": runtime.max_pages_per_part,
            "report_interval": runtime.report_interval,
        },
        provider_metrics=_providers_with_metrics(db, runtime.babeldoc_qps),
    )


@router.get("/runtime", response_model=RuntimeOut, dependencies=[Depends(require_api_key)])
def runtime_config(db: Session = Depends(get_db)):
    return _runtime(db)


@router.put("/runtime", response_model=RuntimeOut, dependencies=[Depends(require_api_key)])
def update_runtime(payload: RuntimeUpdate, db: Session = Depends(get_db)):
    row = _runtime(db)
    values = payload.model_dump(exclude_none=True)
    if "default_provider" in values:
        provider = db.get(ProviderProfile, values["default_provider"])
        if not provider or not provider.enabled:
            raise HTTPException(400, "default provider must exist and be enabled")
    if "default_provider_ids" in values:
        clean: list[str] = []
        for provider_id in values["default_provider_ids"]:
            provider_id = str(provider_id).strip()
            if not provider_id or provider_id in clean:
                continue
            provider = db.get(ProviderProfile, provider_id)
            if not provider or not provider.enabled or not provider_is_configured(provider):
                raise HTTPException(400, f"default provider pool contains an unavailable provider: {provider_id}")
            clean.append(provider_id)
        if not clean:
            raise HTTPException(400, "default provider pool cannot be empty")
        values["default_provider_ids"] = clean
        values["default_provider"] = clean[0]
    if "default_provider_strategy" in values:
        strategy = str(values["default_provider_strategy"]).strip().lower()
        if strategy not in {"balanced", "failover"}:
            raise HTTPException(400, "default_provider_strategy must be balanced or failover")
        values["default_provider_strategy"] = strategy
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    manager.submit("runtime-updated")
    return row


@router.get("/workers", response_model=list[WorkerOut], dependencies=[Depends(require_api_key)])
def workers():
    snap = manager.snapshot()
    return [WorkerOut(
        name=snap["name"],
        active_count=snap["active"],
        reserved_count=snap["queued"],
        scheduled_count=0,
        pool={"max_concurrency": get_runtime_max_jobs(), "processes": [os.getpid()]},
        stats={"pid": snap["pid"], "uptime": snap["uptime"], "total": {}},
    )]


def get_runtime_max_jobs() -> int:
    try:
        from ..runtime import get_runtime_snapshot
        return get_runtime_snapshot().max_active_jobs
    except Exception:
        return 1
