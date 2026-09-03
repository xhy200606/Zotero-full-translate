from __future__ import annotations

from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import (AuthPrincipal, device_is_active, issue_client_api_key, normalize_scopes, require_client_scope, require_web_session, revoke_client_api_key, revoke_device_sessions, utcnow)
from ..db import get_db
from ..models import ClientApiKey, Device, Job, RuntimeConfig, UsageEvent
from ..schemas import (AccountSummary, ClientApiKeyCreateRequest, ClientApiKeyCreatedOut, ClientApiKeyOut, ClientApiKeyRotateRequest, ClientProviderInstanceOut, ClientProviderPoolOut, DeviceOut, DeviceUpdateRequest, JobOut, UsageSummary, UserPublic)
from ..services.providers import provider_is_configured
from ..services.quota import quota_manager
from ..services.user_providers import ensure_user_provider_defaults, get_user_translation_settings, provider_metadata
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
    now = utcnow()
    device_rows = db.scalars(select(Device).where(Device.user_id == user_id, Device.revoked.is_(False))).all()
    device_count = sum(1 for device in device_rows if device_is_active(db, device, now=now))
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
    now = utcnow()
    rows = db.scalars(select(Device).where(Device.user_id == principal.user.id, Device.revoked.is_(False)).order_by(desc(Device.last_seen_at))).all()
    active = [x for x in rows if device_is_active(db, x, now=now)]
    return [DeviceOut.model_validate(x).model_copy(update={"current": principal.device_id == x.id}) for x in active]


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


@router.get("/provider-pool", response_model=ClientProviderPoolOut)
def client_provider_pool(
    principal: AuthPrincipal = Depends(require_client_scope("translate")),
    db: Session = Depends(get_db),
):
    if not principal.user_id:
        raise HTTPException(status_code=401, detail="account authentication required")
    rows = ensure_user_provider_defaults(db, principal.user_id)
    settings = get_user_translation_settings(db, principal.user_id)
    default_ids = [str(x) for x in list(settings.default_provider_ids or []) if str(x).strip()]
    runtime = db.get(RuntimeConfig, 1)
    fallback_qps = float(getattr(runtime, "babeldoc_qps", 1) or 1)
    items: list[ClientProviderInstanceOut] = []
    for row in rows:
        if not row.enabled or not provider_is_configured(row):
            continue
        config = dict(row.config or {})
        meta = provider_metadata(row)
        try:
            qps = max(0.1, float(config.get("qps") or fallback_qps))
        except Exception:
            qps = max(0.1, fallback_qps)
        try:
            max_concurrency = max(1, int(config.get("max_concurrency") or 1))
        except Exception:
            max_concurrency = 1
        quota = quota_manager.snapshot(f"user:{principal.user_id}:{row.provider_id}", config)
        items.append(ClientProviderInstanceOut(
            id=row.provider_id,
            kind=row.kind,
            display_name=row.display_name,
            vendor=meta.get("vendor"),
            template_id=meta.get("template_id"),
            qps=qps,
            max_concurrency=max_concurrency,
            quota_status=str(quota.get("status") or "normal"),
            quota_enabled=bool(quota.get("enabled", True)),
            quota_period=str(quota.get("period") or "month"),
            quota_total_chars=quota.get("total_chars"),
            quota_used_chars=quota.get("used_chars"),
            quota_remaining_chars=quota.get("remaining_chars"),
            quota_remaining_percent=quota.get("remaining_percent"),
            quota_reserve_chars=int(quota.get("reserve_chars") or 0),
            quota_low_percent=float(quota.get("low_percent") or 10.0),
            quota_reset_at=quota.get("reset_at"),
            last_test_ok=row.last_test_ok,
            selected_by_default=row.provider_id in default_ids,
        ))
    usable_ids = {item.id for item in items}
    effective_defaults = [pid for pid in default_ids if pid in usable_ids]
    if not effective_defaults:
        effective_defaults = [item.id for item in items]
    strategy = str(settings.default_provider_strategy or "balanced").strip().lower()
    if len(effective_defaults) <= 1:
        strategy = "single"
    elif strategy not in {"balanced", "failover"}:
        strategy = "balanced"
    return ClientProviderPoolOut(
        items=items,
        default_provider_ids=effective_defaults,
        default_provider_strategy=strategy,
    )


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
