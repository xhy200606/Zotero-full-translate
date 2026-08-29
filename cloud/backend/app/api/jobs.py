from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AuthPrincipal, require_client_scope, require_sse_key, utcnow
from ..config import get_settings
from ..db import get_db
from ..events import request_cancel, subscribe
from ..joblog import record_job_event
from ..models import Job, JobEvent, RuntimeConfig, TranslationVersion, UsageEvent, UserDocumentBinding, UserProviderProfile
from ..schemas import JobEventOut, JobList, JobOut, JobReuseLookup, JobReuseLookupRequest
from ..services.document_bindings import bind_completed_job, normalize_doi, resolve_bound_job
from ..services.providers import provider_is_configured
from ..services.user_providers import ensure_user_provider_defaults, get_user_translation_settings
from ..services.usage import record_usage
from ..storage import path_for, put_bytes
from ..task_manager import manager

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


def _result_bytes(job: Job) -> int:
    total = 0
    for key in {job.mono_key, job.dual_key}:
        if not key:
            continue
        try:
            p = path_for(key)
            if p.is_file():
                total += int(p.stat().st_size)
        except Exception:
            pass
    return total


def _ensure_result_hashes(db: Session, job: Job) -> Job:
    """Populate result hashes once so clients can avoid redundant PDF downloads."""
    changed = False
    for kind in ("mono", "dual"):
        key = getattr(job, f"{kind}_key", None)
        current = getattr(job, f"{kind}_sha256", None)
        if current or not key:
            continue
        try:
            path = path_for(key)
            if not path.is_file():
                continue
            h = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            setattr(job, f"{kind}_sha256", h.hexdigest())
            changed = True
        except Exception:
            continue
    if changed:
        db.commit()
    return job


def _find_reusable_job(
    db: Session, document_doi: str, lang_in: str, lang_out: str, pages: str | None,
    output_mode: str, *, prefer_user_id: str | None = None,
) -> Job | None:
    doi = normalize_doi(document_doi)
    if not doi:
        return None
    rows = db.scalars(
        select(Job).where(
            Job.document_doi == doi,
            Job.lang_in == lang_in,
            Job.lang_out == lang_out,
            Job.status == "COMPLETED",
        ).order_by(Job.cache_hit.asc(), desc(Job.finished_at), desc(Job.created_at)).limit(100)
    ).all()
    target_pages = _normalized_pages(pages)
    fallback = None
    for row in rows:
        if _normalized_pages(row.pages) != target_pages or not _job_has_output(row, output_mode):
            continue
        if prefer_user_id and row.user_id == prefer_user_id:
            return row
        if fallback is None:
            fallback = row
    return fallback


def _can_access(job: Job, principal: AuthPrincipal) -> bool:
    if principal.service or principal.is_admin:
        return True
    return bool(principal.user_id and job.user_id == principal.user_id)


def _require_job(db: Session, job_id: str, principal: AuthPrincipal) -> Job:
    job = db.get(Job, job_id)
    if not job or not _can_access(job, principal):
        raise HTTPException(404, "job not found")
    return job


def serialize(job: Job) -> JobOut:
    provider_ids = list(job.provider_ids or []) or [job.provider]
    return JobOut.model_validate(job).model_copy(update={
        "provider_ids": provider_ids,
        "provider_strategy": job.provider_strategy or ("balanced" if len(provider_ids) > 1 else "single"),
        "has_mono": bool(job.mono_key),
        "has_dual": bool(job.dual_key),
        "source_bytes": int(job.source_bytes or 0),
        "result_bytes": int(job.result_bytes or _result_bytes(job)),
        "cache_hit": bool(job.cache_hit or job.reused_from_job_id),
    })


def _clone_reuse_job(
    db: Session,
    reusable: Job,
    principal: AuthPrincipal,
    *,
    filename: str,
    client_id: str | None,
    client_request_id: str | None,
    client_item_key: str | None,
    source_bytes: int = 0,
    event_type: str = "document_reuse",
) -> Job:
    if client_id and client_request_id:
        existing_query = select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id)
        if principal.user_id:
            existing_query = existing_query.where(Job.user_id == principal.user_id)
        existing = db.scalar(existing_query)
        if existing and _can_access(existing, principal):
            return existing

    reusable.reuse_count = int(reusable.reuse_count or 0) + 1
    _ensure_result_hashes(db, reusable)
    result_bytes = int(reusable.result_bytes or _result_bytes(reusable))
    job = Job(
        id=uuid.uuid4().hex,
        filename=(filename or "document.pdf")[:512],
        status="COMPLETED",
        stage="shared translation cache hit",
        progress=100.0,
        stage_progress=100.0,
        lang_in=reusable.lang_in,
        lang_out=reusable.lang_out,
        pages=reusable.pages,
        output_mode=reusable.output_mode,
        provider=reusable.provider,
        provider_ids=list(reusable.provider_ids or []) or [reusable.provider],
        provider_strategy=reusable.provider_strategy or "single",
        qps=reusable.qps,
        pool_workers=reusable.pool_workers,
        source_bytes=max(0, int(source_bytes or 0)),
        result_bytes=result_bytes,
        cache_hit=True,
        user_id=principal.user_id,
        device_id=principal.device_id,
        api_key_id=principal.api_key_id,
        client_id=(client_id or None),
        client_request_id=(client_request_id or None),
        client_item_key=(client_item_key or None),
                                                                                
                                                            
        source_key=f"doi/{normalize_doi(reusable.document_doi) or 'unknown'}",
        source_sha256=None,
        document_doi=normalize_doi(reusable.document_doi) or None,
        reused_from_job_id=reusable.id,
        mono_key=reusable.mono_key,
        dual_key=reusable.dual_key,
        mono_sha256=reusable.mono_sha256,
        dual_sha256=reusable.dual_sha256,
        metrics={
            "history_reused": True,
            "shared_artifact": True,
            "translation_skipped": True,
            "source_translation_finished_at": reusable.finished_at.isoformat() if reusable.finished_at else None,
        },
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    db.add(job)
    try:
                                                                                 
                                                                              
                                                                        
        db.flush()
        if principal.user_id:
            bind_completed_job(db, job, commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        if client_id and client_request_id:
            query = select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id)
            if principal.user_id:
                query = query.where(Job.user_id == principal.user_id)
            existing = db.scalar(query)
            if existing and _can_access(existing, principal):
                return existing
        raise
    record_job_event(job.id, {
        "type": "reuse",
        "status": "COMPLETED",
        "stage": "shared translation cache hit",
        "progress": 100,
        "shared_artifact": True,
    })
    if principal.user_id:
        record_usage(
            db,
            event_type=event_type,
            user_id=principal.user_id,
            device_id=principal.device_id,
            api_key_id=principal.api_key_id,
            job_id=job.id,
            request_bytes=source_bytes,
            source_sha256=None,
            cache_hit=True,
            details={"shared_artifact": True, "document_doi": job.document_doi},
        )
    return job


@router.post("/lookup", response_model=JobReuseLookup)
def lookup_job(
    payload: JobReuseLookupRequest,
    principal: AuthPrincipal = Depends(require_client_scope("lookup")),
    db: Session = Depends(get_db),
):
    doi = normalize_doi(payload.document_doi)
    if not doi:
        raise HTTPException(400, "document_doi must be a valid DOI")
    if payload.output_mode not in {"mono", "dual", "both"}:
        raise HTTPException(400, "output_mode must be mono, dual, or both")

    if principal.user_id:
        bound = resolve_bound_job(
            db, user_id=principal.user_id, document_doi=doi,
            lang_in=payload.lang_in, lang_out=payload.lang_out,
            pages=payload.pages, output_mode=payload.output_mode,
        )
        if bound is not None and _job_has_output(bound, payload.output_mode):
                                                                                
                                                                               
                                                                                 
            _ensure_result_hashes(db, bound)
            db.commit()
            record_usage(
                db, event_type="document_lock_hit", user_id=principal.user_id,
                device_id=principal.device_id, api_key_id=principal.api_key_id,
                job_id=bound.id, cache_hit=True,
                details={"document_doi": doi, "translation_reused": True},
            )
            return JobReuseLookup(found=True, match="account-document-doi-lock", job=serialize(bound))

    row = _find_reusable_job(
        db, doi, payload.lang_in, payload.lang_out, payload.pages, payload.output_mode,
        prefer_user_id=principal.user_id,
    )
    if row is None:
        if principal.user_id:
            record_usage(
                db, event_type="cache_lookup_miss", user_id=principal.user_id,
                device_id=principal.device_id, api_key_id=principal.api_key_id,
                details={"document_doi": doi, "lang_out": payload.lang_out},
            )
        return JobReuseLookup(found=False, match=None, job=None)

    if principal.user_id and row.user_id == principal.user_id:
        _ensure_result_hashes(db, row)
                                                                                
                                                                             
        bind_completed_job(db, row, commit=False)
        db.commit()
        record_usage(
            db, event_type="cache_lookup_hit", user_id=principal.user_id,
            device_id=principal.device_id, api_key_id=principal.api_key_id,
            job_id=row.id, cache_hit=True,
            details={"document_doi": doi, "translation_reused": True},
        )
        return JobReuseLookup(found=True, match="same-account-doi+language+pages+output", job=serialize(row))

                                                                                  
                                                                                  
                                                                                   
                                                          
    if principal.user_id:
        record_usage(
            db, event_type="cache_lookup_miss", user_id=principal.user_id,
            device_id=principal.device_id, api_key_id=principal.api_key_id, cache_hit=False,
            details={"shared_candidate_hidden": True, "document_doi": doi, "lang_out": payload.lang_out},
        )
        return JobReuseLookup(found=False, match=None, job=None)
    return JobReuseLookup(found=True, match="doi+language+pages+output", job=serialize(row))


def _resolve_provider_pool(
    db: Session,
    *,
    user_id: str | None,
    provider: str | None,
    providers: str | None,
    provider_strategy: str,
) -> tuple[RuntimeConfig, list[str], str, str]:
    """Resolve the authenticated account's provider pool.

    Server-global provider credentials are intentionally not used for normal jobs.
    Every user owns independent endpoint/key configuration on the 3005 portal.
    """
    runtime = db.get(RuntimeConfig, 1)
    if runtime is None:
        raise HTTPException(503, "runtime configuration is not initialized")
    if not user_id:
        raise HTTPException(401, "an authenticated user account is required for translation")

    ensure_user_provider_defaults(db, user_id)
    user_settings = get_user_translation_settings(db, user_id)
    explicit_pool = bool((providers or "").strip() or (provider or "").strip())
    raw_ids = [x.strip() for x in (providers or "").split(",") if x.strip()]
    if not raw_ids and provider:
        raw_ids = [provider.strip()]
    if not raw_ids:
        raw_ids = list(user_settings.default_provider_ids or [])

                                                                          
                                                                                
                                                               
    if not raw_ids:
        rows = db.scalars(select(UserProviderProfile).where(
            UserProviderProfile.user_id == user_id,
            UserProviderProfile.enabled.is_(True),
        ).order_by(UserProviderProfile.provider_id.asc())).all()
        raw_ids = [row.provider_id for row in rows if provider_is_configured(row)]

    selected_ids: list[str] = []
    for provider_id in raw_ids:
        if provider_id and provider_id not in selected_ids:
            selected_ids.append(provider_id)
    if not selected_ids:
        raise HTTPException(400, "请先在用户中心的“翻译 API”中配置并启用至少一个翻译服务")

    usable_ids: list[str] = []
    for selected_id in selected_ids:
        profile = db.scalar(select(UserProviderProfile).where(
            UserProviderProfile.user_id == user_id,
            UserProviderProfile.provider_id == selected_id,
        ))
        if profile is not None and profile.enabled and provider_is_configured(profile):
            usable_ids.append(selected_id)
        elif explicit_pool:
            raise HTTPException(400, f"当前账户的翻译服务未启用或未配置: {selected_id}")
    selected_ids = usable_ids
    if not selected_ids:
        raise HTTPException(400, "当前账户没有可用的翻译服务；请到用户中心配置 API 地址和密钥")

    strategy = (
        provider_strategy.strip().lower()
        if explicit_pool
        else str(user_settings.default_provider_strategy or "balanced").lower()
    )
    if len(selected_ids) == 1:
        strategy = "single"
    elif strategy not in {"balanced", "failover"}:
        raise HTTPException(400, "provider_strategy must be balanced or failover")
    return runtime, selected_ids, strategy, selected_ids[0]


@router.post("", response_model=JobOut)
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
    document_doi: Annotated[str | None, Form()] = None,
    force_retranslate: Annotated[bool, Form()] = False,
    principal: AuthPrincipal = Depends(require_client_scope("translate")),
    db: Session = Depends(get_db),
):
    if client_id and client_request_id:
        query = select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id)
        if principal.user_id:
            query = query.where(Job.user_id == principal.user_id)
        existing = db.scalar(query)
        if existing and _can_access(existing, principal):
            return serialize(existing)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF files are accepted")
    if output_mode not in {"mono", "dual", "both"}:
        raise HTTPException(400, "output_mode must be mono, dual, or both")

    max_upload_mb = max(1, min(2048, int(get_settings().zft_max_upload_mb or 200)))
    upload_limit = max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(min(1024 * 1024, upload_limit - total_bytes + 1))
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > upload_limit:
            raise HTTPException(413, f"PDF exceeds the {max_upload_mb} MiB server limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "file does not look like a PDF")

    doi = normalize_doi(document_doi)
    reusable = None if force_retranslate or not doi else _find_reusable_job(db, doi, lang_in, lang_out, pages, output_mode)
    if reusable is not None:
                                                                               
                                                                             
                                                                                  
        return serialize(_clone_reuse_job(
            db,
            reusable,
            principal,
            filename=file.filename,
            client_id=client_id,
            client_request_id=client_request_id,
            client_item_key=client_item_key,
            source_bytes=len(data),
            event_type="job_cache_reuse",
        ))

    runtime, selected_ids, strategy, provider_id = _resolve_provider_pool(
        db, user_id=principal.user_id, provider=provider, providers=providers, provider_strategy=provider_strategy
    )

    job_id = uuid.uuid4().hex
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
        source_bytes=len(data),
        result_bytes=0,
        cache_hit=False,
        user_id=principal.user_id,
        device_id=principal.device_id,
        api_key_id=principal.api_key_id,
        client_id=(client_id or None),
        client_request_id=(client_request_id or None),
        client_item_key=(client_item_key or None),
        source_key=source_key,
        source_sha256=None,
        document_doi=doi or None,
        metrics={"force_retranslate": bool(force_retranslate), "ignore_cache": bool(force_retranslate), "document_identity": "doi" if doi else "none"},
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if client_id and client_request_id:
            query = select(Job).where(Job.client_id == client_id, Job.client_request_id == client_request_id)
            if principal.user_id:
                query = query.where(Job.user_id == principal.user_id)
            existing = db.scalar(query)
            if existing and _can_access(existing, principal):
                return serialize(existing)
        raise
    if principal.user_id:
        record_usage(
            db,
            event_type="translation_submit",
            user_id=principal.user_id,
            device_id=principal.device_id,
            api_key_id=principal.api_key_id,
            job_id=job.id,
            request_bytes=len(data),
            source_sha256=None,
            cache_hit=False,
            details={"force_retranslate": bool(force_retranslate), "document_doi": doi or None},
        )
    record_job_event(job_id, {
        "type": "state", "status": "QUEUED", "stage": "queued", "progress": 0,
        "provider_ids": selected_ids, "provider_strategy": strategy,
        "force_retranslate": bool(force_retranslate),
    })
    manager.submit(job_id)
    return serialize(job)


@router.get("", response_model=JobList)
def list_jobs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    provider: str | None = None,
    client_id: str | None = None,
    search: str | None = None,
    active_only: bool = False,
    principal: AuthPrincipal = Depends(require_client_scope("lookup")),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    filters = []
    if principal.user_id and not principal.is_admin:
        filters.append(Job.user_id == principal.user_id)
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
    query = select(Job)
    count_query = select(func.count()).select_from(Job)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(db.scalar(count_query) or 0)
    rows = db.scalars(query.order_by(desc(Job.created_at)).offset(max(offset, 0)).limit(limit)).all()
    return JobList(items=[serialize(x) for x in rows], total=total)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, principal: AuthPrincipal = Depends(require_client_scope("lookup")), db: Session = Depends(get_db)):
    return serialize(_ensure_result_hashes(db, _require_job(db, job_id, principal)))


@router.get("/{job_id}/timeline", response_model=list[JobEventOut])
def job_timeline(job_id: str, limit: int = Query(200, ge=1, le=1000), principal: AuthPrincipal = Depends(require_client_scope("lookup")), db: Session = Depends(get_db)):
    _require_job(db, job_id, principal)
    rows = db.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(desc(JobEvent.id)).limit(limit)).all()
    return list(reversed(rows))


@router.delete("/{job_id}", response_model=JobOut)
def cancel_job(job_id: str, principal: AuthPrincipal = Depends(require_client_scope("translate")), db: Session = Depends(get_db)):
    job = _require_job(db, job_id, principal)
    if job.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return serialize(job)
    was_queued = job.status in {"CREATED", "UPLOADING", "QUEUED"}
    job.status = "CANCELLING"
    job.stage = "cancelling"
    db.commit()
    record_job_event(job_id, {"type": "state", "status": "CANCELLING", "stage": "cancelling", "progress": job.progress})
    request_cancel(job_id)
    if was_queued:
        job.status = "CANCELLED"
        job.stage = "cancelled"
        db.commit()
        record_job_event(job_id, {"type": "state", "status": "CANCELLED", "stage": "cancelled", "progress": job.progress})
    return serialize(job)


@router.delete("/{job_id}/history")
def delete_job_history(job_id: str, principal: AuthPrincipal = Depends(require_client_scope("translate")), db: Session = Depends(get_db)):
    job = _require_job(db, job_id, principal)
    if job.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "active jobs must be cancelled before deleting history")

    versions = list(db.scalars(select(TranslationVersion).where(TranslationVersion.job_id == job.id)).all())
    version_ids = [v.id for v in versions]
    binding_filters = [UserDocumentBinding.bound_job_id == job.id]
    if version_ids:
        binding_filters.append(UserDocumentBinding.current_version_id.in_(version_ids))
    bindings = list(db.scalars(select(UserDocumentBinding).where(or_(*binding_filters))).all())
    foreign_bindings = [b for b in bindings if job.user_id and b.user_id != job.user_id]
    if foreign_bindings:
        raise HTTPException(409, "translation version is still bound by another account")

    candidate_keys = [key for key in {job.source_key, job.mono_key, job.dual_key} if key]
    removable_keys = []
    for key in candidate_keys:
        other = db.scalar(select(func.count()).select_from(Job).where(
            Job.id != job.id,
            or_(Job.source_key == key, Job.mono_key == key, Job.dual_key == key),
        ))
        if not int(other or 0):
            removable_keys.append(key)

    for binding in bindings:
        db.delete(binding)
    for version in versions:
        db.delete(version)
    db.execute(update(Job).where(Job.reused_from_job_id == job.id).values(reused_from_job_id=None))
    db.execute(update(UsageEvent).where(UsageEvent.job_id == job.id).values(job_id=None))
    for event in db.scalars(select(JobEvent).where(JobEvent.job_id == job.id)).all():
        db.delete(event)
    db.delete(job)
    db.commit()

    deleted_files = []
    for key in removable_keys:
        try:
            path = path_for(key)
            if path.is_file():
                path.unlink()
                deleted_files.append(key)
        except Exception:
            pass
    return {"ok": True, "deleted_job_id": job_id, "deleted_files": deleted_files}


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, principal: AuthPrincipal = Depends(require_client_scope("translate")), db: Session = Depends(get_db)):
    job = _require_job(db, job_id, principal)
    if job.status not in {"FAILED", "CANCELLED"}:
        raise HTTPException(409, "only failed or cancelled jobs can be retried")
    provider_ids = list(job.provider_ids or []) or [job.provider]
    for provider_id in provider_ids:
        profile = db.scalar(select(UserProviderProfile).where(
            UserProviderProfile.user_id == job.user_id, UserProviderProfile.provider_id == provider_id
        ))
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
    job.mono_sha256 = None
    job.dual_sha256 = None
    job.result_bytes = 0
    job.cache_hit = False
    job.celery_task_id = None
    db.commit()
    manager.submit(job_id)
    record_job_event(job_id, {"type": "state", "status": "QUEUED", "stage": "retry queued", "progress": 0})
    return serialize(job)


@router.get("/{job_id}/events")
async def job_events(job_id: str, principal: AuthPrincipal = Depends(require_sse_key), db: Session = Depends(get_db)):
    _require_job(db, job_id, principal)

    async def stream():
        yield "retry: 2000\n\n"
        async for message in subscribe(job_id):
            if message is None:
                yield ": heartbeat\n\n"
            else:
                yield f"data: {message}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{job_id}/result/{kind}")
def download_result(job_id: str, kind: str, principal: AuthPrincipal = Depends(require_client_scope("download")), db: Session = Depends(get_db)):
    job = _require_job(db, job_id, principal)
    key = job.mono_key if kind == "mono" else job.dual_key if kind == "dual" else None
    if not key:
        raise HTTPException(404, f"{kind} result is not available")
    path = path_for(key)
    if not path.is_file():
        raise HTTPException(404, "result file is missing from local storage")
    response_bytes = int(path.stat().st_size)
    if principal.user_id:
        record_usage(
            db,
            event_type="result_download",
            user_id=principal.user_id,
            device_id=principal.device_id,
            api_key_id=principal.api_key_id,
            job_id=job.id,
            response_bytes=response_bytes,
            source_sha256=None,
            cache_hit=False,
            details={"kind": kind, "job_cache_hit": bool(job.cache_hit), "document_doi": job.document_doi},
        )
    _ensure_result_hashes(db, job)
    result_sha256 = job.mono_sha256 if kind == "mono" else job.dual_sha256
    stem = job.filename[:-4] if job.filename.lower().endswith(".pdf") else job.filename
    headers = {}
    if result_sha256:
        headers["X-ZFT-Result-SHA256"] = result_sha256
        headers["ETag"] = f'"sha256-{result_sha256}"'
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{stem}.{job.lang_out}.{kind}.pdf",
        headers=headers,
    )
