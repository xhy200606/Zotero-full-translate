from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "device_code", name="uq_devices_user_code"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    device_code: Mapped[str] = mapped_column(String(160), index=True)
    name: Mapped[str] = mapped_column(String(180), default="Zotero device")
    platform: Mapped[str | None] = mapped_column(String(80), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(48), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    device_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ClientApiKey(Base):
    """Long-lived account API key used by Zotero clients.

    Only the SHA-256 hash is stored. The plaintext key is returned exactly once
    when it is created from the authenticated user portal. One key may be used
    by multiple Zotero installations; per-installation device UUIDs are tracked
    separately and never participate in document ownership/binding.
    """
    __tablename__ = "client_api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["translate", "lookup", "download", "account:read"])
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    rotated_from_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    request_bytes: Mapped[int] = mapped_column(Integer, default=0)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("client_id", "client_request_id", name="uq_jobs_client_request"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), index=True, default="CREATED")
    stage: Mapped[str] = mapped_column(String(160), default="created")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0)

    lang_in: Mapped[str] = mapped_column(String(32), default="en")
    lang_out: Mapped[str] = mapped_column(String(32), default="zh-CN")
    pages: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_mode: Mapped[str] = mapped_column(String(16), default="mono")
    provider: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    provider_ids: Mapped[list] = mapped_column(JSON, default=list)
    provider_strategy: Mapped[str] = mapped_column(String(24), default="single")
    qps: Mapped[int] = mapped_column(Integer, default=1)
    pool_workers: Mapped[int] = mapped_column(Integer, default=1)

    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    result_bytes: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

                                                                                   
                                                          
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    client_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    client_item_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    worker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source_key: Mapped[str] = mapped_column(String(1024))
                                                                                
                                                                                
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    document_doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    reused_from_job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    mono_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dual_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mono_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dual_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TranslationVersion(Base):
    """Immutable completed translation revision for a DOI + translation profile."""
    __tablename__ = "translation_versions"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_translation_version_job"),
        Index("ix_translation_versions_doi_profile", "document_doi", "lang_in", "lang_out", "pages_key", "output_mode"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_doi: Mapped[str] = mapped_column(String(255), index=True)
    lang_in: Mapped[str] = mapped_column(String(32), default="en")
    lang_out: Mapped[str] = mapped_column(String(32), default="zh-CN")
    pages_key: Mapped[str] = mapped_column(String(128), default="")
    output_mode: Mapped[str] = mapped_column(String(16), default="mono")
    job_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UserDocumentBinding(Base):
    """Account-level pointer to the current immutable translation revision.

    Cloud 2.3+ uses normalized DOI as the document identity. ``source_sha256`` and
    ``bound_job_id`` remain nullable legacy fields so existing 2.2 databases can
    migrate in place without destructive table rebuilds.
    """
    __tablename__ = "user_document_bindings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "document_doi", "lang_in", "lang_out",
            "pages_key", "output_mode", name="uq_user_document_binding_doi"
        ),
        Index("ix_user_document_binding_doi_profile", "user_id", "document_doi", "lang_in", "lang_out", "pages_key", "output_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    document_doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lang_in: Mapped[str] = mapped_column(String(32), default="en")
    lang_out: Mapped[str] = mapped_column(String(32), default="zh-CN")
    pages_key: Mapped[str] = mapped_column(String(128), default="")
    output_mode: Mapped[str] = mapped_column(String(16), default="mono")
    current_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    bound_job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(40), default="progress")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(160), nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    default_provider: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    default_provider_ids: Mapped[list] = mapped_column(JSON, default=list)
    default_provider_strategy: Mapped[str] = mapped_column(String(24), default="balanced")
    max_active_jobs: Mapped[int] = mapped_column(Integer, default=1)
    babeldoc_qps: Mapped[int] = mapped_column(Integer, default=1)
    pool_max_workers: Mapped[int] = mapped_column(Integer, default=1)
    multi_pool_max_workers: Mapped[int] = mapped_column(Integer, default=12)
    aggregate_qps_cap: Mapped[int] = mapped_column(Integer, default=100)
    quota_aware_dispatch: Mapped[bool] = mapped_column(Boolean, default=True)
    report_interval: Mapped[float] = mapped_column(Float, default=0.5)
    max_pages_per_part: Mapped[int] = mapped_column(Integer, default=50)
    skip_scanned_detection: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_ocr_workaround: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserProviderProfile(Base):
    """Translation-provider credentials/configuration owned by one Cloud account."""
    __tablename__ = "user_provider_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_id", name="uq_user_provider_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    provider_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserTranslationSettings(Base):
    __tablename__ = "user_translation_settings"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    default_provider_ids: Mapped[list] = mapped_column(JSON, default=list)
    default_provider_strategy: Mapped[str] = mapped_column(String(24), default="balanced")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProviderQuotaState(Base):
    __tablename__ = "provider_quota_state"

    provider_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(32), default="")
    local_used_chars: Mapped[int] = mapped_column(Integer, default=0)
    remote_used_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_sync_local_chars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    source: Mapped[str] = mapped_column(String(48), default="local_meter")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TranslationMemoryEntry(Base):
    __tablename__ = "translation_memory"
    __table_args__ = (
        UniqueConstraint("source_hash", "lang_in", "lang_out", "profile_key", name="uq_translation_memory_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    lang_in: Mapped[str] = mapped_column(String(32), default="en")
    lang_out: Mapped[str] = mapped_column(String(32), default="zh-CN")
    profile_key: Mapped[str] = mapped_column(String(80), default="academic-v1")
    source_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text)
    provider_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProviderProfile(Base):
    __tablename__ = "provider_profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
