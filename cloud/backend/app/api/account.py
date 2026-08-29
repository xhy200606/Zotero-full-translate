from __future__ import annotations

from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import (AuthPrincipal, issue_client_api_key, normalize_scopes, require_web_session, revoke_client_api_key, revoke_device_sessions, utcnow)
from ..db import get_db
from ..models import ClientApiKey, Device, Job, UsageEvent
from ..schemas import (AccountSummary, ClientApiKeyCreateRequest, ClientApiKeyCreatedOut, ClientApiKeyOut, ClientApiKeyRotateRequest, DeviceOut, DeviceUpdateRequest, JobOut, UsageSummary, UserPublic)
from ..services.usage import day_bounds, usage_summary

router = APIRouter(prefix="/api/v1/account", tags=["account"])


def _job_out(job: Job) -> JobOut:
    provider_ids = list(job.provider_ids or []) or [job.provider]
    return JobOut.model_validate(job).model_copy(update={
        "provider_ids": provider_ids,
        "provider_strategy": job.provider_strategy or ("balanced" if len(provider_ids) > 1 else "single"),
        "has_mono": bool(job.mono_key),
        "has_dual": bool(job.dual_key),
    })


@router.get("/summary", response_model=AccountSummary)
def account_summary(principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    user_id = principal.user.id
    today = usage_summary(db, user_id=user_id)
    total_jobs = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user_id)) or 0)
    total_cache_hits = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user_id, Job.cache_hit.is_(True))) or 0)
    device_count = int(db.scalar(select(func.count()).select_from(Device).where(Device.user_id == user_id, Device.revoked.is_(False))) or 0)
    now = utcnow()
    api_key_count = int(db.scalar(select(func.count()).select_from(ClientApiKey).where(
        ClientApiKey.user_id == user_id,
        ClientApiKey.revoked_at.is_(None),
        or_(ClientApiKey.expires_at.is_(None), ClientApiKey.expires_at > now),
    )) or 0)
    recent = db.scalars(select(Job).where(Job.user_id == user_id).order_by(desc(Job.created_at)).limit(8)).all()
    return AccountSummary(
        user=UserPublic.model_validate(principal.user),
        today=UsageSummary(**{k: today[k] for k in UsageSummary.model_fields}),
        total_jobs=total_jobs,
        total_cache_hits=total_cache_hits,
        device_count=device_count,
        api_key_count=api_key_count,
        recent_jobs=[_job_out(x) for x in recent],
    )


@router.get("/devices", response_model=list[DeviceOut])
def account_devices(principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    rows = db.scalars(select(Device).where(Device.user_id == principal.user.id, Device.revoked.is_(False)).order_by(desc(Device.last_seen_at))).all()
    return [DeviceOut.model_validate(x).model_copy(update={"current": principal.device_id == x.id}) for x in rows]


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def rename_device(
    device_id: str,
    payload: DeviceUpdateRequest,
    principal: AuthPrincipal = Depends(require_web_session),
    db: Session = Depends(get_db),
):
    device = db.get(Device, device_id)
    if device is None or device.user_id != principal.user.id:
        raise HTTPException(status_code=404, detail="device not found")
    if device.revoked:
        raise HTTPException(status_code=409, detail="device is revoked")
    device.name = payload.name.strip()[:180]
    db.commit()
    return DeviceOut.model_validate(device).model_copy(update={"current": principal.device_id == device.id})


@router.delete("/devices/{device_id}")
def revoke_device(device_id: str, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None or device.user_id != principal.user.id:
        raise HTTPException(status_code=404, detail="device not found")
    if principal.device_id == device.id:
        raise HTTPException(status_code=409, detail="cannot revoke the current device from this session")
    revoke_device_sessions(db, device)
    return {"ok": True}


@router.get("/usage")
def account_usage(days: int = 14, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    days = max(1, min(90, int(days)))
    today_start, _ = day_bounds()
    points = []
    for i in range(days - 1, -1, -1):
        start = today_start - timedelta(days=i)
        end = start + timedelta(days=1)
        data = usage_summary(db, user_id=principal.user.id, start=start, end=end)
        points.append({"date": data["date"], "calls": data["calls"], "bytes": data["total_bytes"], "cache_hits": data["cache_hits"], "jobs": data["jobs"]})
    return {"items": points}


@router.get("/api-keys", response_model=list[ClientApiKeyOut])
def account_api_keys(principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    now = utcnow()
    rows = db.scalars(
        select(ClientApiKey)
        .where(
            ClientApiKey.user_id == principal.user.id,
            ClientApiKey.revoked_at.is_(None),
            or_(ClientApiKey.expires_at.is_(None), ClientApiKey.expires_at > now),
        )
        .order_by(desc(ClientApiKey.created_at))
    ).all()
    return [ClientApiKeyOut.model_validate(row) for row in rows]


@router.post("/api-keys", response_model=ClientApiKeyCreatedOut)
def create_account_api_key(
    payload: ClientApiKeyCreateRequest,
    principal: AuthPrincipal = Depends(require_web_session),
    db: Session = Depends(get_db),
):
    try:
        scopes = normalize_scopes(payload.scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw, row = issue_client_api_key(
        db, principal.user, payload.label, scopes=scopes, expires_in_days=payload.expires_in_days
    )
    db.commit()
    data = ClientApiKeyOut.model_validate(row).model_dump()
    return ClientApiKeyCreatedOut(**data, api_key=raw)


@router.post("/api-keys/{api_key_id}/rotate", response_model=ClientApiKeyCreatedOut)
def rotate_account_api_key(
    api_key_id: str,
    payload: ClientApiKeyRotateRequest,
    principal: AuthPrincipal = Depends(require_web_session),
    db: Session = Depends(get_db),
):
    row = db.get(ClientApiKey, api_key_id)
    if row is None or row.user_id != principal.user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key is already revoked")
    raw, replacement = issue_client_api_key(
        db,
        principal.user,
        row.label,
        scopes=list(row.scopes or []),
        expires_in_days=payload.expires_in_days,
        rotated_from_id=row.id,
    )
                                                                           
                                                                            
                           
    if payload.expires_in_days is None:
        old_expiry = row.expires_at
        replacement.expires_at = (
            old_expiry if old_expiry is None or old_expiry.tzinfo is not None
            else old_expiry.replace(tzinfo=timezone.utc)
        )
    row.revoked_at = utcnow()
    db.commit()
    data = ClientApiKeyOut.model_validate(replacement).model_dump()
    return ClientApiKeyCreatedOut(**data, api_key=raw)


@router.delete("/api-keys/{api_key_id}")
def delete_account_api_key(
    api_key_id: str,
    principal: AuthPrincipal = Depends(require_web_session),
    db: Session = Depends(get_db),
):
    row = db.get(ClientApiKey, api_key_id)
    if row is None or row.user_id != principal.user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    revoke_client_api_key(db, row)
    return {"ok": True}
