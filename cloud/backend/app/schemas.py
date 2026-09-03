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
    document_doi: str | None = None
    reuse_count: int = 0
    source_bytes: int = 0
    result_bytes: int = 0
    cache_hit: bool = False
    user_id: str | None = None
    device_id: str | None = None
    api_key_id: str | None = None
    error_code: str | None
    error_message: str | None
    metrics: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    has_mono: bool = False
    has_dual: bool = False
    mono_sha256: str | None = None
    dual_sha256: str | None = None


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
    custom: bool = False
    configured: bool
    template_id: str | None = None
    vendor: str | None = None
    logo: str | None = None
    description: str = ""
    credential_url: str | None = None
    docs_url: str | None = None
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


class ProviderCreateRequest(BaseModel):
    template_id: str | None = Field(default=None, min_length=2, max_length=60)
    kind: str | None = Field(default=None, min_length=2, max_length=40)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class ProviderCatalogItem(BaseModel):
    template_id: str
    kind: str
    vendor: str
    logo: str
    display_name: str
    description: str
    credential_url: str | None = None
    docs_url: str | None = None


class UserTranslationSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    default_provider_ids: list[str] = Field(default_factory=list)
    default_provider_strategy: str = "balanced"
    updated_at: datetime


class UserTranslationSettingsUpdate(BaseModel):
    default_provider_ids: list[str] = Field(default_factory=list)
    default_provider_strategy: str = "balanced"


class ClientProviderInstanceOut(BaseModel):
    id: str
    kind: str
    display_name: str
    vendor: str | None = None
    template_id: str | None = None
    qps: float = 1.0
    max_concurrency: int = 1
    quota_status: str = "normal"
    quota_enabled: bool = True
    quota_period: str = "month"
    quota_total_chars: int | None = None
    quota_used_chars: int | None = None
    quota_remaining_chars: int | None = None
    quota_remaining_percent: float | None = None
    quota_reserve_chars: int = 0
    quota_low_percent: float = 10.0
    quota_reset_at: str | None = None
    last_test_ok: bool | None = None
    selected_by_default: bool = False


class ClientProviderPoolOut(BaseModel):
    items: list[ClientProviderInstanceOut] = Field(default_factory=list)
    default_provider_ids: list[str] = Field(default_factory=list)
    default_provider_strategy: str = "balanced"


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
    document_doi: str = Field(min_length=3, max_length=255)
    lang_in: str = "en"
    lang_out: str = "zh-CN"
    pages: str | None = None
    output_mode: str = "mono"
    filename: str | None = Field(default=None, max_length=512)
    client_id: str | None = Field(default=None, max_length=128)
    client_request_id: str | None = Field(default=None, max_length=160)
    client_item_key: str | None = Field(default=None, max_length=160)


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


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    email: str | None = None
    display_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    device_code: str
    name: str
    platform: str | None = None
    app_version: str | None = None
    revoked: bool
    created_at: datetime
    last_seen_at: datetime
    current: bool = False


class DeviceUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)


class ClientApiKeyCreateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["translate", "lookup", "download", "account:read"])
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ClientApiKeyRotateRequest(BaseModel):
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ClientApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key_prefix: str
    label: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    rotated_from_id: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ClientApiKeyCreatedOut(ClientApiKeyOut):
    api_key: str


class AuthCapabilities(BaseModel):
    registration_enabled: bool = False
    setup_required: bool = False
    first_registered_user_is_admin: bool = True
    token_ttl_days: int = 180
    device_authentication: bool = True
    shared_translation_cache: bool = True
    client_api_keys: bool = True
    account_document_lock: bool = True


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)
    device_code: str = Field(min_length=4, max_length=160)
    device_aliases: list[str] = Field(default_factory=list, max_length=8)
    device_name: str = Field(default="Browser", min_length=1, max_length=180)
    platform: str | None = Field(default=None, max_length=80)
    app_version: str | None = Field(default=None, max_length=48)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=512)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)
    device_code: str = Field(min_length=4, max_length=160)
    device_aliases: list[str] = Field(default_factory=list, max_length=8)
    device_name: str = Field(default="Browser", min_length=1, max_length=180)
    platform: str | None = Field(default=None, max_length=80)
    app_version: str | None = Field(default=None, max_length=48)


class AuthSessionOut(BaseModel):
    expires_at: datetime
    user: UserPublic
    device: DeviceOut


class ClientAuthOut(BaseModel):
    user: UserPublic
    api_key_id: str
    key_prefix: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    device: DeviceOut | None = None


class BootstrapAdminRequest(BaseModel):
    username: str = Field(default="admin", min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=512)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default="Administrator", max_length=120)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=512)


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=512)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)
    role: str = "user"
    is_active: bool = True


class AdminUpdateUserRequest(BaseModel):
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=512)


class UsageSummary(BaseModel):
    date: str
    calls: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    total_bytes: int = 0
    cache_hits: int = 0
    translated_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0


class AccountSummary(BaseModel):
    user: UserPublic
    today: UsageSummary
    total_jobs: int = 0
    total_cache_hits: int = 0
    device_count: int = 0
    api_key_count: int = 0
    recent_jobs: list[JobOut] = Field(default_factory=list)


class AdminUserRow(BaseModel):
    user: UserPublic
    device_count: int = 0
    api_key_count: int = 0
    today_calls: int = 0
    today_request_bytes: int = 0
    today_response_bytes: int = 0
    today_bytes: int = 0
    today_cache_hits: int = 0
    total_jobs: int = 0
    total_cache_hits: int = 0


class AdminSummary(BaseModel):
    date: str
    total_users: int = 0
    new_users_today: int = 0
    active_users_today: int = 0
    active_devices_24h: int = 0
    active_api_keys: int = 0
    today_calls: int = 0
    today_request_bytes: int = 0
    today_response_bytes: int = 0
    today_bytes: int = 0
    today_cache_hits: int = 0
    today_jobs: int = 0
    today_completed: int = 0
    today_failed: int = 0
    all_completed_jobs: int = 0
    shared_cache_reuse_total: int = 0


class DailyUsagePoint(BaseModel):
    date: str
    calls: int = 0
    bytes: int = 0
    cache_hits: int = 0
    jobs: int = 0


class AdminUserDetail(BaseModel):
    user: UserPublic
    today: UsageSummary
    device_count: int = 0
    api_key_count: int = 0
    total_jobs: int = 0
    total_cache_hits: int = 0
    devices: list[DeviceOut] = Field(default_factory=list)
    recent_jobs: list[JobOut] = Field(default_factory=list)
    usage: list[DailyUsagePoint] = Field(default_factory=list)

