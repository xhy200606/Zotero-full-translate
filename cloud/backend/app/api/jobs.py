from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_api_key, require_sse_key
from ..db import get_db
from ..events import request_cancel, subscribe
from ..joblog import record_job_event
from ..models import Job, JobEvent, ProviderProfile, RuntimeConfig
from ..schemas import JobEventOut, JobList, JobOut, JobReuseLookup, JobReuseLookupRequest
from ..storage import path_for, put_bytes
from ..task_manager import manager
from ..services.providers import provider_is_configured

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _normalized_pages(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _job_has_output(job: Job, output_mode: str) -> bool:
    if output_mode == "dual":
        return bool(job.dual_key and path_for(job.dual_key).is_file())
    if output_mode == "both":
        return bool(job.mono_key and job.dual_key and path_for(job.mono_key).is_file() and path_for(job.dual_key).is_file())
    return bool(job.mono_key and path_for(job.mono_key).is_file())


def _find_reusable_job(db: Session, source_sha256: str, lang_in: str, lang_out: str, pages: str | None, output_mode: str) -> Job | None:
    rows = db.scalars(
        select(Job).where(
            Job.source_sha256 == source_sha256.lower(),
            Job.lang_in == lang_in,
            Job.lang_out == lang_out,
            Job.status == "COMPLETED",
        ).order_by(desc(Job.finished_at), desc(Job.created_at)).limit(30)
    ).all()
    target_pages = _normalized_pages(pages)
    for row in rows:
        if _normalized_pages(row.pages) != target_pages:
            continue
        if _job_has_output(row, output_mode):
            return row
    return None


@router.post("/lookup", response_model=JobReuseLookup, dependencies=[Depends(require_api_key)])
def lookup_job(payload: JobReuseLookupRequest, db: Session = Depends(get_db)):
    sha = payload.source_sha256.strip().lower()
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise HTTPException(400, "source_sha256 must be a 64-character hex SHA-256")
    if payload.output_mode not in {"mono", "dual", "both"}:
        raise HTTPException(400, "output_mode must be mono, dual, or both")
    row = _find_reusable_job(db, sha, payload.lang_in, payload.lang_out, payload.pages, payload.output_mode)
    if row is None:
        return JobReuseLookup(found=False, match=None, job=None)
    return JobReuseLookup(found=True, match="source_sha256+language+pages+output", job=serialize(row))



def serialize(job: Job) -> JobOut:
    provider_ids = list(job.provider_ids or []) or [job.provider]
    return JobOut.model_validate(job).model_copy(update={
        "provider_ids": provider_ids,
        "provider_strategy": job.provider_strategy or ("balanced" if len(provider_ids) > 1 else "single"),
        "has_mono": bool(job.mono_key), "has_dual": bool(job.dual_key),
    })


@router.post("", response_model=JobOut, dependencies=[Depends(require_api_key)])
async def create_job(
    file: Annotated[UploadFile, File()],
    lang_in: Annotated[str, Form()] = "en",
    lang_out: Annotated[str, Form()] = "zh-CN",
    pages: Annotated[str | None, Form()] = None,
    output_mode: Annotated[str, Form()] = "mono",
    provider: Annotated[str | None, Form()] = None,
    providers: Annotated[str | None, Form()] = None,
    provider_strategy: Annotated[str, Form()] = "balanced",
    client_id: Annotated[str | None, Form()] = None,
    client_request_id: Annotated[str | None, Form()] = None,
    client_item_key: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    if client_id and client_request_id:
        existing = db.scalar(select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id))
        if existing:
            return serialize(existing)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF files are accepted")
    if output_mode not in {"mono", "dual", "both"}:
        raise HTTPException(400, "output_mode must be mono, dual, or both")

    runtime = db.get(RuntimeConfig, 1)
    if runtime is None:
        raise HTTPException(503, "runtime configuration is not initialized")
    explicit_pool = bool((providers or "").strip() or (provider or "").strip())
    raw_ids = [x.strip() for x in (providers or "").split(",") if x.strip()]
    if not raw_ids:
        if provider:
            raw_ids = [provider.strip()]
        else:
            raw_ids = list(getattr(runtime, "default_provider_ids", None) or [runtime.default_provider])
    selected_ids: list[str] = []
    for provider_id in raw_ids:
        if provider_id and provider_id not in selected_ids:
            selected_ids.append(provider_id)
    if not selected_ids:
        raise HTTPException(400, "at least one translation provider is required")

    # Explicit Web/API selections fail fast. The server-side default pool is more
    # tolerant: providers disabled after the pool was saved are skipped so thin
    # clients such as Zotero can still use the remaining engines.
    usable_ids: list[str] = []
    for selected_id in selected_ids:
        profile = db.get(ProviderProfile, selected_id)
        if profile is not None and profile.enabled and provider_is_configured(profile):
            usable_ids.append(selected_id)
        elif explicit_pool:
            raise HTTPException(400, f"translation provider is not enabled/configured: {selected_id}")
    selected_ids = usable_ids
    if not selected_ids:
        raise HTTPException(400, "no enabled translation provider is available in the selected/default pool")

    strategy = provider_strategy.strip().lower() if explicit_pool else str(getattr(runtime, "default_provider_strategy", None) or "balanced").lower()
    if len(selected_ids) == 1:
        strategy = "single"
    elif strategy not in {"balanced", "failover"}:
        raise HTTPException(400, "provider_strategy must be balanced or failover")
    provider_id = selected_ids[0]

    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "file does not look like a PDF")
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "PDF exceeds the 200 MiB server limit")

    source_sha256 = hashlib.sha256(data).hexdigest()
    reusable = _find_reusable_job(db, source_sha256, lang_in, lang_out, pages, output_mode)
    job_id = uuid.uuid4().hex
    if reusable is not None:
        reusable.reuse_count = int(reusable.reuse_count or 0) + 1
        job = Job(
            id=job_id,
            filename=file.filename,
            status="COMPLETED",
            stage="history cache hit",
            progress=100.0,
            stage_progress=100.0,
            lang_in=lang_in, lang_out=lang_out, pages=_normalized_pages(pages), output_mode=output_mode,
            provider=reusable.provider,
            provider_ids=list(reusable.provider_ids or []) or [reusable.provider],
            provider_strategy=reusable.provider_strategy or "single",
            qps=reusable.qps, pool_workers=reusable.pool_workers,
            client_id=(client_id or None), client_request_id=(client_request_id or None), client_item_key=(client_item_key or None),
            source_key=reusable.source_key, source_sha256=source_sha256, reused_from_job_id=reusable.id,
            mono_key=reusable.mono_key, dual_key=reusable.dual_key,
            metrics={"history_reused": True, "reused_from_job_id": reusable.id, "upload_fallback": True},
            started_at=reusable.started_at, finished_at=reusable.finished_at,
        )
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if client_id and client_request_id:
                existing = db.scalar(select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id))
                if existing:
                    return serialize(existing)
            raise
        record_job_event(job_id, {"type": "reuse", "status": "COMPLETED", "stage": "history cache hit", "progress": 100, "reused_from_job_id": reusable.id})
        return serialize(job)

    source_key = f"inputs/{job_id}/source.pdf"
    put_bytes(source_key, data, "application/pdf")
    job = Job(
        id=job_id,
        filename=file.filename,
        status="QUEUED",
        stage="queued",
        progress=0,
        lang_in=lang_in,
        lang_out=lang_out,
        pages=pages or None,
        output_mode=output_mode,
        provider=provider_id,
        provider_ids=selected_ids,
        provider_strategy=strategy,
        qps=max(1, runtime.babeldoc_qps),
        pool_workers=(getattr(runtime, "multi_pool_max_workers", 12) if len(selected_ids) > 1 else max(1, runtime.pool_max_workers)),
        client_id=(client_id or None),
        client_request_id=(client_request_id or None),
        client_item_key=(client_item_key or None),
        source_key=source_key,
        source_sha256=source_sha256,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if client_id and client_request_id:
            existing = db.scalar(select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id))
            if existing:
                return serialize(existing)
        raise
    record_job_event(job_id, {"type": "state", "status": "QUEUED", "stage": "queued", "progress": 0, "provider_ids": selected_ids, "provider_strategy": strategy})
    job.celery_task_id = None
    db.commit()
    manager.submit(job_id)
    return serialize(job)


@router.get("", response_model=JobList, dependencies=[Depends(require_api_key)])
def list_jobs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    provider: str | None = None,
    client_id: str | None = None,
    search: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    query = select(Job)
    count_query = select(func.count()).select_from(Job)
    filters = []
    if status:
        filters.append(Job.status == status)
    if provider:
        filters.append(Job.provider == provider)
    if client_id:
        filters.append(Job.client_id == client_id)
    if active_only:
        filters.append(Job.status.not_in(["COMPLETED", "FAILED", "CANCELLED"]))
    if search:
        needle = f"%{search.strip()}%"
        filters.append(or_(Job.filename.ilike(needle), Job.id.ilike(needle), Job.client_item_key.ilike(needle)))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.order_by(desc(Job.created_at)).offset(max(offset, 0)).limit(limit)).all()
    return JobList(items=[serialize(x) for x in rows], total=total)


@router.get("/{job_id}", response_model=JobOut, dependencies=[Depends(require_api_key)])
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return serialize(job)


@router.get("/{job_id}/timeline", response_model=list[JobEventOut], dependencies=[Depends(require_api_key)])
def job_timeline(job_id: str, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    if not db.get(Job, job_id):
        raise HTTPException(404, "job not found")
    rows = db.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(desc(JobEvent.id)).limit(limit)
    ).all()
    return list(reversed(rows))


@router.delete("/{job_id}", response_model=JobOut, dependencies=[Depends(require_api_key)])
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return serialize(job)
    was_queued = job.status in {"CREATED", "UPLOADING", "QUEUED"}
    job.status = "CANCELLING"
    job.stage = "cancelling"
    db.commit()
    payload = {"type": "state", "status": "CANCELLING", "stage": "cancelling", "progress": job.progress}
    record_job_event(job_id, payload)
    request_cancel(job_id)
    if was_queued:
        job.status = "CANCELLED"
        job.stage = "cancelled"
        db.commit()
        record_job_event(job_id, {"type": "state", "status": "CANCELLED", "stage": "cancelled", "progress": job.progress})
    return serialize(job)


@router.post("/{job_id}/retry", response_model=JobOut, dependencies=[Depends(require_api_key)])
def retry_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status not in {"FAILED", "CANCELLED"}:
        raise HTTPException(409, "only failed or cancelled jobs can be retried")
    provider_ids = list(job.provider_ids or []) or [job.provider]
    for provider_id in provider_ids:
        profile = db.get(ProviderProfile, provider_id)
        if not profile or not profile.enabled or not provider_is_configured(profile):
            raise HTTPException(409, f"provider is no longer enabled/configured: {provider_id}")
    job.status = "QUEUED"
    job.stage = "queued"
    job.progress = 0
    job.stage_progress = 0
    job.error_code = None
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    job.mono_key = None
    job.dual_key = None
    job.celery_task_id = None
    db.commit()
    manager.submit(job_id)
    record_job_event(job_id, {"type": "state", "status": "QUEUED", "stage": "retry queued", "progress": 0})
    return serialize(job)


@router.get("/{job_id}/events", dependencies=[Depends(require_sse_key)])
async def job_events(job_id: str, db: Session = Depends(get_db)):
    if not db.get(Job, job_id):
        raise HTTPException(404, "job not found")

    async def stream():
        yield "retry: 2000\n\n"
        async for message in subscribe(job_id):
            if message is None:
                yield ": heartbeat\n\n"
            else:
                yield f"data: {message}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{job_id}/result/{kind}", dependencies=[Depends(require_api_key)])
def download_result(job_id: str, kind: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    key = job.mono_key if kind == "mono" else job.dual_key if kind == "dual" else None
    if not key:
        raise HTTPException(404, f"{kind} result is not available")
    path = path_for(key)
    if not path.is_file():
        raise HTTPException(404, "result file is missing from local storage")
    stem = job.filename[:-4] if job.filename.lower().endswith(".pdf") else job.filename
    return FileResponse(path, media_type="application/pdf", filename=f"{stem}.{job.lang_out}.{kind}.pdf")
