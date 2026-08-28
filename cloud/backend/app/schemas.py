from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    status: str
    stage: str
    progress: float
    stage_progress: float
    lang_in: str
    lang_out: str
    pages: str | None
    output_mode: str
    provider: str
    provider_ids: list[str] | None = None
    provider_strategy: str = "single"
    qps: int
    pool_workers: int
    client_id: str | None = None
    client_request_id: str | None = None
    client_item_key: str | None = None
    worker_name: str | None = None
    source_sha256: str | None = None
    reused_from_job_id: str | None = None
    reuse_count: int = 0
    error_code: str | None
    error_message: str | None
    metrics: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    has_mono: bool = False
    has_dual: bool = False


class JobList(BaseModel):
    items: list[JobOut]
    total: int


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: str
    event_type: str
    status: str | None
    stage: str | None
    progress: float | None
    payload: dict
    created_at: datetime


class SystemStatus(BaseModel):
    ok: bool
    version: str
    database: bool
    redis: bool
    storage: bool
    translator_provider: str
    queue_depth: int
    active_jobs: int
    queued_jobs: int
    completed_jobs: int
    failed_jobs: int
    server_limits: dict = Field(default_factory=dict)
    provider_metrics: list[dict] = Field(default_factory=list)


class RuntimeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    default_provider: str
    default_provider_ids: list[str] = Field(default_factory=list)
    default_provider_strategy: str = "balanced"
    max_active_jobs: int
    babeldoc_qps: int
    pool_max_workers: int
    multi_pool_max_workers: int
    aggregate_qps_cap: int
    quota_aware_dispatch: bool = True
    report_interval: float
    max_pages_per_part: int
    skip_scanned_detection: bool
    auto_ocr_workaround: bool
    updated_at: datetime


class RuntimeUpdate(BaseModel):
    default_provider: str | None = None
    default_provider_ids: list[str] | None = None
    default_provider_strategy: str | None = None
    max_active_jobs: int | None = Field(default=None, ge=1, le=64)
    babeldoc_qps: int | None = Field(default=None, ge=1, le=1000)
    pool_max_workers: int | None = Field(default=None, ge=1, le=1000)
    multi_pool_max_workers: int | None = Field(default=None, ge=1, le=1000)
    aggregate_qps_cap: int | None = Field(default=None, ge=1, le=5000)
    quota_aware_dispatch: bool | None = None
    report_interval: float | None = Field(default=None, ge=0.1, le=30)
    max_pages_per_part: int | None = Field(default=None, ge=1, le=500)
    skip_scanned_detection: bool | None = None
    auto_ocr_workaround: bool | None = None


class ProviderOut(BaseModel):
    id: str
    kind: str
    display_name: str
    enabled: bool
    configured: bool
    config: dict[str, Any]
    secret_fields: dict[str, bool]
    last_test_ok: bool | None = None
    last_test_message: str | None = None
    last_test_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    quota: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class ProviderUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = None


class ProviderTestOut(BaseModel):
    ok: bool
    provider: str
    message: str
    sample: str | None = None


class WorkerOut(BaseModel):
    name: str
    online: bool = True
    active_count: int = 0
    reserved_count: int = 0
    scheduled_count: int = 0
    pool: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)


class JobReuseLookupRequest(BaseModel):
    source_sha256: str = Field(min_length=64, max_length=64)
    lang_in: str = "en"
    lang_out: str = "zh-CN"
    pages: str | None = None
    output_mode: str = "mono"


class JobReuseLookup(BaseModel):
    found: bool
    match: str | None = None
    job: JobOut | None = None


class TranslationMemoryStats(BaseModel):
    entries: int = 0
    stored_source_chars: int = 0
    reuse_hits: int = 0
    process_hits: int = 0
    process_misses: int = 0
    process_writes: int = 0
