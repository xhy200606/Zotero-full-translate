from __future__ import annotations

import hashlib

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Job
from ..storage import path_for


def sha256_path(path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def backfill_source_hashes(limit: int = 5000) -> int:
    """Populate SHA-256 for historical jobs created before v1.4.

    Source PDFs and results are already under /data, so this lets Zotero reuse
    older completed translations without re-uploading or re-translating them.
    """
    updated = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Job).where(Job.source_sha256.is_(None)).order_by(Job.created_at.desc()).limit(limit)
        ).all()
        cache: dict[str, str] = {}
        for job in rows:
            key = str(job.source_key or "")
            if not key:
                continue
            try:
                if key not in cache:
                    path = path_for(key)
                    if not path.is_file():
                        continue
                    cache[key] = sha256_path(path)
                job.source_sha256 = cache[key]
                updated += 1
            except Exception:
                continue
        if updated:
            db.commit()
    return updated
