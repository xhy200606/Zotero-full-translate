from __future__ import annotations

from .db import SessionLocal
from .models import JobEvent


def record_job_event(job_id: str, payload: dict) -> None:
    try:
        with SessionLocal() as db:
            db.add(JobEvent(
                job_id=job_id,
                event_type=str(payload.get("type") or "progress")[:40],
                status=(str(payload.get("status"))[:32] if payload.get("status") is not None else None),
                stage=(str(payload.get("stage"))[:160] if payload.get("stage") is not None else None),
                progress=(float(payload.get("progress")) if payload.get("progress") is not None else None),
                payload=payload,
            ))
            db.commit()
    except Exception:
                                                                         
        pass
