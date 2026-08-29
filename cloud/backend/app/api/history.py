from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..auth import AuthPrincipal, require_admin, require_client_scope
from ..db import get_db
from ..models import Job
from ..services.document_bindings import normalize_doi
from ..services.translation_memory import translation_memory

router = APIRouter(prefix="/api/v1/history", tags=["history"])


@router.get("/translation-memory", dependencies=[Depends(require_admin)])
def translation_memory_history(limit: int = Query(50, ge=1, le=500)):
    return {
        "stats": translation_memory.stats(),
        "items": translation_memory.recent(limit),
    }


@router.get("/documents")
def completed_documents(
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_client_scope("lookup")),
    db: Session = Depends(get_db),
):
    """Return DOI-keyed completed translations visible to the current account.

    Cloud 2.3 no longer computes source-PDF hashes for document identity. Result
    hashes remain on jobs for byte-exact local translated-PDF verification.
    """
    query = select(Job).where(Job.status == "COMPLETED", Job.document_doi.is_not(None))
    if principal.user_id and not principal.is_admin:
        query = query.where(Job.user_id == principal.user_id)
    rows = db.scalars(query.order_by(desc(Job.finished_at), desc(Job.created_at)).limit(limit * 3)).all()
    out = []
    seen = set()
    for job in rows:
        doi = normalize_doi(job.document_doi)
        if not doi:
            continue
        key = (doi, job.lang_in, job.lang_out, job.pages or "", job.output_mode)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "document_doi": doi,
            "lang_in": job.lang_in,
            "lang_out": job.lang_out,
            "pages": job.pages,
            "output_mode": job.output_mode,
            "job_id": job.id,
            "filename": job.filename,
            "finished_at": job.finished_at,
            "mono_sha256": job.mono_sha256,
            "dual_sha256": job.dual_sha256,
        })
        if len(out) >= limit:
            break
    return {"items": out, "total": len(out), "identity": "doi"}
