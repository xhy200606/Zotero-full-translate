from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Job, UsageEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dashboard_timezone() -> ZoneInfo:
    """Return the configured IANA timezone, falling back to UTC safely."""
    name = str(get_settings().zft_timezone or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def day_bounds(reference: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC bounds for the configured local calendar day.

    SQL rows are stored in UTC. Dashboards, however, should interpret “today” in
    the administrator-selected timezone (Asia/Shanghai by default), not UTC.
    """
    tz = dashboard_timezone()
    now = reference or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    local_start = datetime(local.year, local.month, local.day, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def local_date_label(instant: datetime) -> str:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(dashboard_timezone()).date().isoformat()


def record_usage(
    db: Session,
    *,
    event_type: str,
    user_id: str | None = None,
    device_id: str | None = None,
    api_key_id: str | None = None,
    job_id: str | None = None,
    request_bytes: int = 0,
    response_bytes: int = 0,
    source_sha256: str | None = None,
    cache_hit: bool = False,
    details: dict | None = None,
    commit: bool = True,
) -> UsageEvent:
    row = UsageEvent(
        user_id=user_id,
        device_id=device_id,
        api_key_id=api_key_id,
        job_id=job_id,
        event_type=str(event_type or "api_call")[:40],
        request_bytes=max(0, int(request_bytes or 0)),
        response_bytes=max(0, int(response_bytes or 0)),
        source_sha256=(source_sha256.lower() if source_sha256 else None),
        cache_hit=bool(cache_hit),
        details=details or {},
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def usage_summary(
    db: Session,
    *,
    user_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    if start is None or end is None:
        start, end = day_bounds()
    filters = [
        UsageEvent.created_at >= start,
        UsageEvent.created_at < end,
                                                                           
        UsageEvent.event_type.not_in(["login", "register"]),
    ]
    if user_id is not None:
        filters.append(UsageEvent.user_id == user_id)
    row = db.execute(
        select(
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.request_bytes), 0),
            func.coalesce(func.sum(UsageEvent.response_bytes), 0),
            func.coalesce(func.sum(case((UsageEvent.cache_hit.is_(True), 1), else_=0)), 0),
        ).where(*filters)
    ).one()
    calls, request_bytes, response_bytes, cache_hits = map(int, row)

    job_filters = [Job.created_at >= start, Job.created_at < end]
    if user_id is not None:
        job_filters.append(Job.user_id == user_id)
    jobs = int(db.scalar(select(func.count()).select_from(Job).where(*job_filters)) or 0)
    completed = int(db.scalar(select(func.count()).select_from(Job).where(*job_filters, Job.status == "COMPLETED")) or 0)
    failed = int(db.scalar(select(func.count()).select_from(Job).where(*job_filters, Job.status == "FAILED")) or 0)
    translated = int(db.scalar(select(func.count()).select_from(Job).where(*job_filters, Job.cache_hit.is_(False))) or 0)
    return {
        "date": local_date_label(start),
        "calls": calls,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "total_bytes": request_bytes + response_bytes,
        "cache_hits": cache_hits,
        "translated_jobs": translated,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "jobs": jobs,
    }
