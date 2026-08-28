from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..models import Job
from ..services.translation_memory import translation_memory

router = APIRouter(prefix="/api/v1/history", tags=["history"])


@router.get("/translation-memory", dependencies=[Depends(require_api_key)])
def translation_memory_history(limit: int = Query(50, ge=1, le=500)):
    return {
        "stats": translation_memory.stats(),
        "items": translation_memory.recent(limit),
    }


@router.get("/documents", dependencies=[Depends(require_api_key)])
def document_history(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Job)
        .where(Job.status == "COMPLETED", Job.source_sha256.is_not(None))
        .order_by(desc(Job.finished_at), desc(Job.created_at))
        .limit(limit)
    ).all()
    items = []
    seen = set()
    for job in rows:
        key = (job.source_sha256, job.lang_in, job.lang_out, job.pages or "")
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "job_id": job.id,
            "filename": job.filename,
            "source_sha256": job.source_sha256,
            "lang_in": job.lang_in,
            "lang_out": job.lang_out,
            "pages": job.pages,
            "has_mono": bool(job.mono_key),
            "has_dual": bool(job.dual_key),
            "provider_ids": list(job.provider_ids or []) or [job.provider],
            "provider_strategy": job.provider_strategy,
            "reuse_count": int(job.reuse_count or 0),
            "finished_at": job.finished_at,
        })
    return {"items": items, "total": len(items)}
