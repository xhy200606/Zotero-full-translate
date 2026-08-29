from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Job, TranslationVersion, UserDocumentBinding

_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


def normalize_doi(value: str | None) -> str:
    value = str(value or "").strip()
    value = _DOI_PREFIX_RE.sub("", value).strip().strip("<>[]{}()")
                                                                             
                                                                                
    value = value.rstrip(".,; ").lower()
    if not value or len(value) > 255 or not _DOI_RE.match(value):
        return ""
    return value


def pages_key(value: str | None) -> str:
    return str(value or "").strip()


def ensure_translation_version(db: Session, job: Job) -> TranslationVersion | None:
    doi = normalize_doi(job.document_doi)
    if not doi or job.status != "COMPLETED":
        return None
    existing = db.scalar(select(TranslationVersion).where(TranslationVersion.job_id == job.id))
    if existing is not None:
        return existing
    version = TranslationVersion(
        id=uuid.uuid4().hex,
        document_doi=doi,
        lang_in=job.lang_in,
        lang_out=job.lang_out,
        pages_key=pages_key(job.pages),
        output_mode=job.output_mode,
        job_id=job.id,
        created_by_user_id=job.user_id,
        created_at=job.finished_at or datetime.now(timezone.utc),
    )
    db.add(version)
    db.flush()
    return version


def bind_completed_job(db: Session, job: Job, *, commit: bool = True) -> UserDocumentBinding | None:
    """Atomically point the account binding at a completed immutable version.

    When called from the translation worker with ``commit=False``, the caller can
    commit the job's COMPLETED state, version creation and binding pointer in one
    database transaction.
    """
    doi = normalize_doi(job.document_doi)
    if not job.user_id or not doi or job.status != "COMPLETED":
        if commit:
            db.commit()
        return None
    version = ensure_translation_version(db, job)
    if version is None:
        return None
    binding = db.scalar(select(UserDocumentBinding).where(
        UserDocumentBinding.user_id == job.user_id,
        UserDocumentBinding.document_doi == doi,
        UserDocumentBinding.lang_in == job.lang_in,
        UserDocumentBinding.lang_out == job.lang_out,
        UserDocumentBinding.pages_key == pages_key(job.pages),
        UserDocumentBinding.output_mode == job.output_mode,
    ))
    if binding is None:
        binding = UserDocumentBinding(
            user_id=job.user_id,
            document_doi=doi,
            source_sha256=None,
            lang_in=job.lang_in,
            lang_out=job.lang_out,
            pages_key=pages_key(job.pages),
            output_mode=job.output_mode,
            current_version_id=version.id,
            bound_job_id=job.id,
        )
        db.add(binding)
    else:
        binding.current_version_id = version.id
        binding.bound_job_id = job.id                            
        binding.updated_at = datetime.now(timezone.utc)
    db.flush()
    if commit:
        db.commit()
    return binding


def resolve_bound_job(
    db: Session,
    *,
    user_id: str,
    document_doi: str,
    lang_in: str,
    lang_out: str,
    pages: str | None,
    output_mode: str,
) -> Job | None:
    doi = normalize_doi(document_doi)
    if not doi:
        return None
    binding = db.scalar(select(UserDocumentBinding).where(
        UserDocumentBinding.user_id == user_id,
        UserDocumentBinding.document_doi == doi,
        UserDocumentBinding.lang_in == lang_in,
        UserDocumentBinding.lang_out == lang_out,
        UserDocumentBinding.pages_key == pages_key(pages),
        UserDocumentBinding.output_mode == output_mode,
    ))
    if binding is None:
        return None
    job_id = binding.bound_job_id
    if binding.current_version_id:
        version = db.get(TranslationVersion, binding.current_version_id)
        if version is not None:
            job_id = version.job_id
    if not job_id:
        return None
    job = db.get(Job, job_id)
    return job if job is not None and job.status == "COMPLETED" else None
