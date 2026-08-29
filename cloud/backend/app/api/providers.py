from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import AuthPrincipal, require_web_session
from ..crypto import decrypt_json, encrypt_json
from ..db import get_db
from ..models import RuntimeConfig, UserProviderProfile
from ..schemas import (
    ProviderCatalogItem,
    ProviderCreateRequest,
    ProviderOut,
    ProviderTestOut,
    ProviderUpdate,
    UserTranslationSettingsOut,
    UserTranslationSettingsUpdate,
)
from ..services.provider_security import validate_outbound_url
from ..services.providers import provider_is_configured, test_provider
from ..services.quota import quota_manager
from ..services.rate_limit import gate
from ..services.user_providers import (
    PROVIDER_CATALOG,
    catalog_item,
    ensure_user_provider_defaults,
    get_user_translation_settings,
    provider_metadata,
    touch_user_translation_settings,
)

router = APIRouter(prefix="/api/v1/account/providers", tags=["account-providers"])

SECRET_FIELDS = {
    "baidu": {"app_id", "secret_key", "api_key"},
    "openai_compatible": {"api_key"},
    "tencent": {"api_key", "secret_id", "secret_key"},
    "volcengine": {"api_key"},
    "aliyun": {"access_key_id", "access_key_secret"},
}
CONFIG_FIELDS = {
    "baidu": {"endpoint", "auth_mode", "model_type", "service_type", "reference", "domain", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "openai_compatible": {"base_url", "model", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "tencent": {"auth_mode", "tmt_endpoint", "tmt_region", "tmt_version", "project_id", "max_chars", "base_url", "model", "hunyuan_endpoint", "hunyuan_model", "field", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "volcengine": {"endpoint", "resource_id", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "aliyun": {"endpoint", "path", "scene", "max_chars", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
}


def _scope(user_id: str, provider_id: str) -> str:
    return f"user:{user_id}:{provider_id}"


def _row(db: Session, user_id: str, provider_id: str) -> UserProviderProfile:
    ensure_user_provider_defaults(db, user_id)
    row = db.scalar(select(UserProviderProfile).where(
        UserProviderProfile.user_id == user_id,
        UserProviderProfile.provider_id == provider_id,
    ))
    if row is None:
        raise HTTPException(404, "provider not found")
    return row


def _secret_flags(row: UserProviderProfile) -> dict[str, bool]:
    try:
        secrets = decrypt_json(row.secret_payload)
    except Exception:
        secrets = {}
    return {key: bool(secrets.get(key)) for key in SECRET_FIELDS.get(row.kind, {"api_key"})}


def _qps(row: UserProviderProfile, fallback: int = 1) -> float:
    try:
        return max(0.1, float((row.config or {}).get("qps") or fallback))
    except Exception:
        return float(max(1, fallback))


def serialize(row: UserProviderProfile, fallback_qps: int) -> ProviderOut:
    scope = _scope(row.user_id, row.provider_id)
    meta = provider_metadata(row)
    return ProviderOut(
        id=row.provider_id,
        kind=row.kind,
        display_name=row.display_name,
        enabled=row.enabled,
        custom=bool(getattr(row, "is_custom", False)),
        configured=provider_is_configured(row),
        template_id=meta.get("template_id"),
        vendor=meta.get("vendor"),
        logo=meta.get("logo"),
        description=meta.get("description") or "",
        credential_url=meta.get("credential_url"),
        docs_url=meta.get("docs_url"),
        config=dict(row.config or {}),
        secret_fields=_secret_flags(row),
        last_test_ok=row.last_test_ok,
        last_test_message=row.last_test_message,
        last_test_at=row.last_test_at,
        metrics=gate.snapshot(scope, _qps(row, fallback_qps)),
        quota=quota_manager.snapshot(scope, row.config or {}),
        updated_at=row.updated_at,
    )


def _validate_provider_urls(kind: str, config: dict) -> None:
    fields: tuple[str, ...]
    if kind == "openai_compatible":
        fields = ("base_url",)
    elif kind == "baidu":
        fields = ("endpoint",)
    elif kind == "tencent":
        fields = ("tmt_endpoint", "base_url", "hunyuan_endpoint")
    elif kind == "volcengine":
        fields = ("endpoint",)
    elif kind == "aliyun":
        fields = ("endpoint",)
    else:
        fields = ()
    for field in fields:
        value = str(config.get(field) or "").strip()
        if value:
            config[field] = validate_outbound_url(value, field=field)


def _normalize_config(row: UserProviderProfile, payload: ProviderUpdate) -> None:
    if payload.display_name is not None:
        row.display_name = payload.display_name.strip()
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.config is not None:
        allowed = CONFIG_FIELDS.get(row.kind, set())
        merged = dict(row.config or {})
        for key, value in payload.config.items():
            if key in allowed and value is not None:
                merged[key] = value
        if "qps" in merged:
            merged["qps"] = max(0.1, min(1000.0, float(merged["qps"])))
        if "max_concurrency" in merged:
            merged["max_concurrency"] = max(1, min(1000, int(merged["max_concurrency"])))
        for key in ("quota_total_chars", "quota_reserve_chars"):
            if key in merged:
                try:
                    merged[key] = max(0, int(float(merged.get(key) or 0)))
                except Exception:
                    merged[key] = 0
        if "quota_low_percent" in merged:
            try:
                merged["quota_low_percent"] = max(1.0, min(99.0, float(merged.get("quota_low_percent") or 10)))
            except Exception:
                merged["quota_low_percent"] = 10.0
        if "quota_period" in merged:
            merged["quota_period"] = "account" if str(merged.get("quota_period") or "month").lower() == "account" else "month"
        if row.kind == "baidu":
            service_type = str(merged.get("service_type") or "general").lower()
            if service_type not in {"general", "machine", "llm", "domain"}:
                service_type = "general"
            merged["service_type"] = service_type
            if service_type in {"machine", "llm"}:
                merged["auth_mode"] = "api_key"
                merged["model_type"] = "nmt" if service_type == "machine" else "llm"
                merged["endpoint"] = str(merged.get("endpoint") or "https://fanyi-api.baidu.com/ait/api/aiTextTranslate").strip()
            elif service_type == "domain":
                merged["auth_mode"] = "sign"
                merged["model_type"] = "nmt"
                merged["endpoint"] = str(merged.get("endpoint") or "https://fanyi-api.baidu.com/api/trans/vip/fieldtranslate").strip()
                merged["domain"] = str(merged.get("domain") or "academic").strip().lower()
            else:
                merged["auth_mode"] = "sign"
                merged["model_type"] = "nmt"
                merged["endpoint"] = str(merged.get("endpoint") or "https://fanyi-api.baidu.com/api/trans/vip/translate").strip()
        if row.kind == "tencent":
            mode = str(merged.get("auth_mode") or "tmt_tc3").lower()
            if mode == "legacy_tc3":
                mode = "tmt_tc3"
            merged["auth_mode"] = mode if mode in {"tmt_tc3", "tokenhub", "hunyuan_tc3"} else "tmt_tc3"
            merged.setdefault("tmt_endpoint", "https://tmt.tencentcloudapi.com")
            merged.setdefault("tmt_region", "ap-beijing")
            merged.setdefault("tmt_version", "2018-03-21")
            merged["project_id"] = max(0, int(merged.get("project_id") or 0))
            merged["max_chars"] = max(200, min(1950, int(merged.get("max_chars") or 1900)))
            merged.setdefault("base_url", "https://tokenhub.tencentmaas.com/v1")
            merged.setdefault("model", "hy-mt2-plus")
            merged.setdefault("hunyuan_endpoint", "https://hunyuan.ai.tencentcloudapi.com")
            merged.setdefault("hunyuan_model", "hunyuan-translation-lite")
        if row.kind == "volcengine":
            merged["endpoint"] = str(merged.get("endpoint") or "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate").strip()
            merged["resource_id"] = str(merged.get("resource_id") or "volc.speech.mt").strip()
        _validate_provider_urls(row.kind, merged)
        row.config = merged
    if payload.secrets is not None:
        try:
            current = decrypt_json(row.secret_payload)
        except Exception:
            current = {}
        allowed = SECRET_FIELDS.get(row.kind, set())
        for key, value in payload.secrets.items():
            if key not in allowed or value is None:
                continue
            clean = str(value).strip()
            if not clean:
                current.pop(key, None)
            else:
                current[key] = clean
        row.secret_payload = encrypt_json(current) if current else None
    row.updated_at = datetime.now(timezone.utc)


@router.get("/catalog", response_model=list[ProviderCatalogItem])
def provider_catalog(principal: AuthPrincipal = Depends(require_web_session)):
    del principal
    return [ProviderCatalogItem(template_id=template_id, **{key: value for key, value in item.items() if key != "config"}) for template_id, item in PROVIDER_CATALOG.items()]


@router.get("", response_model=list[ProviderOut])
def list_providers(principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    rows = ensure_user_provider_defaults(db, principal.user_id)
    runtime = db.get(RuntimeConfig, 1)
    fallback = runtime.babeldoc_qps if runtime else 1
    return [serialize(row, fallback) for row in rows]


@router.post("", response_model=ProviderOut)
def create_provider(payload: ProviderCreateRequest, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    template_id = str(payload.template_id or "").strip()
    template = catalog_item(template_id)
    if template is None and payload.kind:
        fallback = next((dict(item) for item in PROVIDER_CATALOG.values() if item.get("kind") == str(payload.kind).strip().lower()), None)
        template = fallback
        template_id = str((fallback or {}).get("config", {}).get("template_id") or "")
    if template is None:
        raise HTTPException(400, "请选择受支持的翻译 API 模板")
    provider_id = f"custom_{uuid.uuid4().hex[:12]}"
    display_name = str(payload.display_name or template["display_name"]).strip()
    config = dict(template["config"])
    config["template_id"] = template_id
    _validate_provider_urls(str(template["kind"]), config)
    row = UserProviderProfile(
        user_id=principal.user_id,
        provider_id=provider_id,
        kind=str(template["kind"]),
        display_name=display_name,
        enabled=False,
        config=config,
        secret_payload=None,
        is_custom=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    runtime = db.get(RuntimeConfig, 1)
    return serialize(row, runtime.babeldoc_qps if runtime else 1)


@router.delete("/{provider_id}")
def delete_provider(provider_id: str, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    row = _row(db, principal.user_id, provider_id)
    if not row.is_custom:
        raise HTTPException(409, "built-in provider profiles cannot be deleted")
    settings = get_user_translation_settings(db, principal.user_id)
    settings.default_provider_ids = [x for x in list(settings.default_provider_ids or []) if x != provider_id]
    touch_user_translation_settings(settings)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.put("/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: str, payload: ProviderUpdate, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    row = _row(db, principal.user_id, provider_id)
    try:
        _normalize_config(row, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    runtime = db.get(RuntimeConfig, 1)
    return serialize(row, runtime.babeldoc_qps if runtime else 1)


@router.get("/{provider_id}/quota")
def get_provider_quota(provider_id: str, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    row = _row(db, principal.user_id, provider_id)
    return quota_manager.snapshot(_scope(principal.user_id, provider_id), row.config or {})


@router.post("/{provider_id}/quota/reset")
def reset_provider_quota(provider_id: str, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    row = _row(db, principal.user_id, provider_id)
    return quota_manager.reset_local_usage(_scope(principal.user_id, provider_id), row.config or {})


@router.post("/{provider_id}/test", response_model=ProviderTestOut)
def test_provider_endpoint(provider_id: str, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    row = _row(db, principal.user_id, provider_id)
    if not row.enabled:
        raise HTTPException(409, "provider is disabled")
    now = datetime.now(timezone.utc)
    scope = _scope(principal.user_id, provider_id)
    try:
        sample = test_provider(provider_id, principal.user_id)
        row.last_test_ok = True
        row.last_test_message = "连接和翻译测试成功"
        row.last_test_at = now
        db.commit()
        quota_manager.clear_status(scope, row.config or {})
        return ProviderTestOut(ok=True, provider=provider_id, message="连接和翻译测试成功", sample=sample)
    except Exception as exc:
        message = str(exc)[:500]
        low = message.lower()
        if "54004" in low or "please recharge" in low:
            quota_manager.mark(scope, row.config or {}, "exhausted", message)
        elif "unsynchronized" in low or "servicenotactivated" in low or "invalid sign" in low or "54001" in low:
            quota_manager.mark(scope, row.config or {}, "unavailable", message)
        row.last_test_ok = False
        row.last_test_message = message
        row.last_test_at = now
        db.commit()
        return ProviderTestOut(ok=False, provider=provider_id, message=message)


@router.get("/settings/default", response_model=UserTranslationSettingsOut)
def get_default_provider_settings(principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    return get_user_translation_settings(db, principal.user_id)


@router.put("/settings/default", response_model=UserTranslationSettingsOut)
def update_default_provider_settings(payload: UserTranslationSettingsUpdate, principal: AuthPrincipal = Depends(require_web_session), db: Session = Depends(get_db)):
    ensure_user_provider_defaults(db, principal.user_id)
    clean: list[str] = []
    for provider_id in payload.default_provider_ids:
        provider_id = str(provider_id).strip()
        if not provider_id or provider_id in clean:
            continue
        row = _row(db, principal.user_id, provider_id)
        if not row.enabled or not provider_is_configured(row):
            raise HTTPException(400, f"provider is not enabled/configured: {provider_id}")
        clean.append(provider_id)
    strategy = str(payload.default_provider_strategy or "balanced").strip().lower()
    if len(clean) <= 1:
        strategy = "single"
    elif strategy not in {"balanced", "failover"}:
        raise HTTPException(400, "default_provider_strategy must be balanced or failover")
    row = get_user_translation_settings(db, principal.user_id)
    row.default_provider_ids = clean
    row.default_provider_strategy = strategy
    touch_user_translation_settings(row)
    db.commit()
    db.refresh(row)
    return row
