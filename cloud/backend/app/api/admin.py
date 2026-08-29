from __future__ import annotations

import uuid
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import AuthPrincipal, hash_password, normalize_username, require_admin, utcnow
from ..db import get_db
from ..models import AuthToken, ClientApiKey, Device, Job, UsageEvent, User
from ..schemas import (
    AdminCreateUserRequest,
    AdminSummary,
    AdminUpdateUserRequest,
    AdminUserDetail,
    AdminUserRow,
    DailyUsagePoint,
    DeviceOut,
    JobOut,
    UsageSummary,
    UserPublic,
)
from ..services.usage import day_bounds, usage_summary

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _job_out(job: Job) -> JobOut:
    provider_ids = list(job.provider_ids or []) or [job.provider]
    return JobOut.model_validate(job).model_copy(update={
        "provider_ids": provider_ids,
        "provider_strategy": job.provider_strategy or ("balanced" if len(provider_ids) > 1 else "single"),
        "has_mono": bool(job.mono_key),
        "has_dual": bool(job.dual_key),
        "cache_hit": bool(job.cache_hit or job.reused_from_job_id),
    })


@router.get("/summary", response_model=AdminSummary)
def admin_summary(_: AuthPrincipal = Depends(require_admin), db: Session = Depends(get_db)):
    start, end = day_bounds()
    total_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    new_users_today = int(db.scalar(select(func.count()).select_from(User).where(User.created_at >= start, User.created_at < end)) or 0)
    active_users_today = int(db.scalar(select(func.count(func.distinct(UsageEvent.user_id))).where(UsageEvent.created_at >= start, UsageEvent.created_at < end, UsageEvent.user_id.is_not(None))) or 0)
    active_devices_24h = int(db.scalar(select(func.count()).select_from(Device).where(Device.revoked.is_(False), Device.last_seen_at >= utcnow() - timedelta(hours=24))) or 0)
    now = utcnow()
    active_api_keys = int(db.scalar(select(func.count()).select_from(ClientApiKey).join(User, ClientApiKey.user_id == User.id).where(
        ClientApiKey.revoked_at.is_(None),
        or_(ClientApiKey.expires_at.is_(None), ClientApiKey.expires_at > now),
        User.is_active.is_(True),
    )) or 0)
    today = usage_summary(db, start=start, end=end)
    today_jobs = int(db.scalar(select(func.count()).select_from(Job).where(Job.created_at >= start, Job.created_at < end)) or 0)
    all_completed = int(db.scalar(select(func.count()).select_from(Job).where(Job.status == "COMPLETED")) or 0)
    shared_reuse = int(db.scalar(select(func.count()).select_from(Job).where(Job.cache_hit.is_(True))) or 0)
    return AdminSummary(
        date=today["date"],
        total_users=total_users,
        new_users_today=new_users_today,
        active_users_today=active_users_today,
        active_devices_24h=active_devices_24h,
        active_api_keys=active_api_keys,
        today_calls=today["calls"],
        today_request_bytes=today["request_bytes"],
        today_response_bytes=today["response_bytes"],
        today_bytes=today["total_bytes"],
        today_cache_hits=today["cache_hits"],
        today_jobs=today_jobs,
        today_completed=today["completed_jobs"],
        today_failed=today["failed_jobs"],
        all_completed_jobs=all_completed,
        shared_cache_reuse_total=shared_reuse,
    )


@router.get("/users", response_model=list[AdminUserRow])
def list_users(
    search: str | None = Query(default=None, max_length=120),
    _: AuthPrincipal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(User)
    if search:
        needle = f"%{search.strip()}%"
        query = query.where((User.username.ilike(needle)) | (User.email.ilike(needle)) | (User.display_name.ilike(needle)))
    users = db.scalars(query.order_by(desc(User.created_at)).limit(500)).all()
    start, end = day_bounds()
    out = []
    for user in users:
        today = usage_summary(db, user_id=user.id, start=start, end=end)
        devices = int(db.scalar(select(func.count()).select_from(Device).where(Device.user_id == user.id, Device.revoked.is_(False))) or 0)
        api_keys = int(db.scalar(select(func.count()).select_from(ClientApiKey).where(
            ClientApiKey.user_id == user.id, ClientApiKey.revoked_at.is_(None),
            or_(ClientApiKey.expires_at.is_(None), ClientApiKey.expires_at > utcnow()),
        )) or 0)
        total_jobs = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id)) or 0)
        total_cache_hits = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id, Job.cache_hit.is_(True))) or 0)
        out.append(AdminUserRow(
            user=UserPublic.model_validate(user),
            device_count=devices,
            api_key_count=api_keys,
            today_calls=today["calls"],
            today_request_bytes=today["request_bytes"],
            today_response_bytes=today["response_bytes"],
            today_bytes=today["total_bytes"],
            today_cache_hits=today["cache_hits"],
            total_jobs=total_jobs,
            total_cache_hits=total_cache_hits,
        ))
    return out


@router.post("/users", response_model=UserPublic)
def create_user(payload: AdminCreateUserRequest, _: AuthPrincipal = Depends(require_admin), db: Session = Depends(get_db)):
    username = normalize_username(payload.username)
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    role = payload.role if payload.role in {"user", "admin"} else "user"
    user = User(
        id=uuid.uuid4().hex,
        username=username,
        email=(payload.email.strip() if payload.email else None),
        display_name=(payload.display_name.strip() if payload.display_name else None),
        password_hash=hash_password(payload.password),
        role=role,
        is_active=bool(payload.is_active),
    )
    db.add(user)
    db.commit()
    return UserPublic.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserPublic)
def update_user(user_id: str, payload: AdminUpdateUserRequest, principal: AuthPrincipal = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.role is not None:
        if payload.role not in {"user", "admin"}:
            raise HTTPException(status_code=400, detail="role must be user or admin")
        if principal.user and principal.user.id == user.id and payload.role != "admin":
            raise HTTPException(status_code=409, detail="cannot remove your own administrator role")
        user.role = payload.role
    revoke_sessions = False
    if payload.is_active is not None:
        if principal.user and principal.user.id == user.id and not payload.is_active:
            raise HTTPException(status_code=409, detail="cannot disable your own account")
        if user.is_active and not payload.is_active:
            revoke_sessions = True
        user.is_active = payload.is_active
    if payload.email is not None:
        user.email = payload.email.strip() or None
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
    if payload.password:
        user.password_hash = hash_password(payload.password)
        revoke_sessions = True
    if revoke_sessions:
        now = utcnow()
        rows = db.scalars(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.revoked_at.is_(None))).all()
        for row in rows:
            row.revoked_at = now
    db.commit()
    return UserPublic.model_validate(user)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def user_detail(
    user_id: str,
    days: int = 14,
    _: AuthPrincipal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    days = max(1, min(90, int(days)))
    today = usage_summary(db, user_id=user_id)
    devices = db.scalars(select(Device).where(Device.user_id == user_id).order_by(desc(Device.last_seen_at))).all()
    recent = db.scalars(select(Job).where(Job.user_id == user_id).order_by(desc(Job.created_at)).limit(12)).all()
    total_jobs = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user_id)) or 0)
    api_key_count = int(db.scalar(select(func.count()).select_from(ClientApiKey).where(
        ClientApiKey.user_id == user_id, ClientApiKey.revoked_at.is_(None),
        or_(ClientApiKey.expires_at.is_(None), ClientApiKey.expires_at > utcnow()),
    )) or 0)
    total_cache_hits = int(db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user_id, Job.cache_hit.is_(True))) or 0)
    today_start, _ = day_bounds()
    points: list[DailyUsagePoint] = []
    for i in range(days - 1, -1, -1):
        start = today_start - timedelta(days=i)
        end = start + timedelta(days=1)
        data = usage_summary(db, user_id=user_id, start=start, end=end)
        points.append(DailyUsagePoint(
            date=data["date"], calls=data["calls"], bytes=data["total_bytes"],
            cache_hits=data["cache_hits"], jobs=data["jobs"],
        ))
    return AdminUserDetail(
        user=UserPublic.model_validate(user),
        today=UsageSummary(**{k: today[k] for k in UsageSummary.model_fields}),
        device_count=len([d for d in devices if not d.revoked]),
        api_key_count=api_key_count,
        total_jobs=total_jobs,
        total_cache_hits=total_cache_hits,
        devices=[DeviceOut.model_validate(d) for d in devices],
        recent_jobs=[_job_out(j) for j in recent],
        usage=points,
    )


@router.get("/users/{user_id}/usage", response_model=list[DailyUsagePoint])
def user_usage(user_id: str, days: int = 30, _: AuthPrincipal = Depends(require_admin), db: Session = Depends(get_db)):
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    days = max(1, min(180, int(days)))
    today_start, _ = day_bounds()
    out = []
    for i in range(days - 1, -1, -1):
        start = today_start - timedelta(days=i)
        end = start + timedelta(days=1)
        data = usage_summary(db, user_id=user_id, start=start, end=end)
        out.append(DailyUsagePoint(date=data["date"], calls=data["calls"], bytes=data["total_bytes"], cache_hits=data["cache_hits"], jobs=data["jobs"]))
    return out
