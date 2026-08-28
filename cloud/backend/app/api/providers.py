from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..crypto import decrypt_json, encrypt_json
from ..db import get_db
from ..models import ProviderProfile, RuntimeConfig
from ..schemas import ProviderOut, ProviderTestOut, ProviderUpdate
from ..services.providers import test_provider
from ..services.rate_limit import gate
from ..services.quota import quota_manager

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])

SECRET_FIELDS = {
    "baidu": {"app_id", "secret_key", "api_key"},
    "openai_compatible": {"api_key"},
    "tencent": {"api_key", "secret_id", "secret_key"},
    "volcengine": {"api_key"},
    "aliyun": {"access_key_id", "access_key_secret"},
}
CONFIG_FIELDS = {
    "baidu": {"endpoint", "auth_mode", "model_type", "reference", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "openai_compatible": {"base_url", "model", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "tencent": {"auth_mode", "tmt_endpoint", "tmt_region", "tmt_version", "project_id", "max_chars", "base_url", "model", "hunyuan_endpoint", "hunyuan_model", "field", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "volcengine": {"endpoint", "resource_id", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
    "aliyun": {"endpoint", "path", "scene", "max_chars", "qps", "max_concurrency", "quota_enabled", "quota_total_chars", "quota_reserve_chars", "quota_low_percent", "quota_period"},
}


def _secret_flags(row: ProviderProfile) -> dict[str, bool]:
    secrets = decrypt_json(row.secret_payload)
    return {key: bool(secrets.get(key)) for key in SECRET_FIELDS.get(row.kind, {"api_key"})}


def _configured(row: ProviderProfile) -> bool:
    flags = _secret_flags(row)
    if row.kind == "baidu":
        auth_mode = str((row.config or {}).get("auth_mode") or "sign").lower()
        return bool(flags.get("app_id") and (flags.get("api_key") if auth_mode == "api_key" else flags.get("secret_key")))
    if row.kind == "tencent":
        auth_mode = str((row.config or {}).get("auth_mode") or "tmt_tc3").lower()
        return bool(flags.get("api_key")) if auth_mode == "tokenhub" else bool(flags.get("secret_id") and flags.get("secret_key"))
    return bool(flags) and all(flags.values())


def _qps(row: ProviderProfile, fallback: int = 1) -> float:
    try:
        return max(0.1, float((row.config or {}).get("qps") or fallback))
    except Exception:
        return float(max(1, fallback))


def serialize(row: ProviderProfile, fallback_qps: int) -> ProviderOut:
    return ProviderOut(
        id=row.id, kind=row.kind, display_name=row.display_name, enabled=row.enabled,
        configured=_configured(row), config=dict(row.config or {}), secret_fields=_secret_flags(row),
        last_test_ok=row.last_test_ok, last_test_message=row.last_test_message, last_test_at=row.last_test_at,
        metrics=gate.snapshot(row.id, _qps(row, fallback_qps)),
        quota=quota_manager.snapshot(row.id, row.config or {}),
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[ProviderOut], dependencies=[Depends(require_api_key)])
def list_providers(db: Session = Depends(get_db)):
    runtime = db.get(RuntimeConfig, 1)
    fallback = runtime.babeldoc_qps if runtime else 1
    rows = db.query(ProviderProfile).order_by(ProviderProfile.id.asc()).all()
    return [serialize(x, fallback) for x in rows]


@router.put("/{provider_id}", response_model=ProviderOut, dependencies=[Depends(require_api_key)])
def update_provider(provider_id: str, payload: ProviderUpdate, db: Session = Depends(get_db)):
    row = db.get(ProviderProfile, provider_id)
    if row is None:
        raise HTTPException(404, "provider not found")
    if payload.display_name is not None:
        row.display_name = payload.display_name.strip()
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.config is not None:
        allowed = CONFIG_FIELDS.get(row.kind, set())
        merged = dict(row.config or {})
        for k, v in payload.config.items():
            if k in allowed and v is not None:
                merged[k] = v
        if "qps" in merged:
            merged["qps"] = max(0.1, min(1000.0, float(merged["qps"])))
        if "max_concurrency" in merged:
            merged["max_concurrency"] = max(1, min(1000, int(merged["max_concurrency"])))
        if "quota_total_chars" in merged:
            try: merged["quota_total_chars"] = max(0, int(float(merged.get("quota_total_chars") or 0)))
            except Exception: merged["quota_total_chars"] = 0
        if "quota_reserve_chars" in merged:
            try: merged["quota_reserve_chars"] = max(0, int(float(merged.get("quota_reserve_chars") or 0)))
            except Exception: merged["quota_reserve_chars"] = 0
        if "quota_low_percent" in merged:
            try: merged["quota_low_percent"] = max(1.0, min(99.0, float(merged.get("quota_low_percent") or 10)))
            except Exception: merged["quota_low_percent"] = 10.0
        if "quota_period" in merged:
            merged["quota_period"] = "account" if str(merged.get("quota_period") or "month").lower() == "account" else "month"
        if row.kind == "baidu":
            mode = str(merged.get("auth_mode") or "sign").lower()
            merged["auth_mode"] = mode if mode in {"sign", "api_key"} else "sign"
            if not str(merged.get("endpoint") or "").strip():
                merged["endpoint"] = "https://fanyi-api.baidu.com/ait/api/aiTextTranslate" if merged["auth_mode"] == "api_key" else "https://fanyi-api.baidu.com/api/trans/vip/translate"
            if str(merged.get("model_type") or "llm").lower() not in {"llm", "nmt"}:
                merged["model_type"] = "llm"
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
            if not str(merged.get("base_url") or "").strip():
                merged["base_url"] = "https://tokenhub.tencentmaas.com/v1"
            if not str(merged.get("model") or "").strip():
                merged["model"] = "hy-mt2-plus"
            merged.setdefault("hunyuan_endpoint", "https://hunyuan.ai.tencentcloudapi.com")
            merged.setdefault("hunyuan_model", "hunyuan-translation-lite")
        if row.kind == "volcengine":
            merged["endpoint"] = str(merged.get("endpoint") or "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate").strip()
            merged["resource_id"] = str(merged.get("resource_id") or "volc.speech.mt").strip()
        row.config = merged
    if payload.secrets is not None:
        current = decrypt_json(row.secret_payload)
        allowed = SECRET_FIELDS.get(row.kind, set())
        for key, value in payload.secrets.items():
            if key not in allowed or value is None:
                continue
            value = str(value).strip()
            if value == "":
                current.pop(key, None)
            else:
                current[key] = value
        row.secret_payload = encrypt_json(current) if current else None
    row.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(row)
    runtime = db.get(RuntimeConfig, 1)
    return serialize(row, runtime.babeldoc_qps if runtime else 1)


@router.post("/{provider_id}/test", response_model=ProviderTestOut, dependencies=[Depends(require_api_key)])
def test_provider_endpoint(provider_id: str, db: Session = Depends(get_db)):
    row = db.get(ProviderProfile, provider_id)
    if row is None:
        raise HTTPException(404, "provider not found")
    if not row.enabled:
        raise HTTPException(409, "provider is disabled")
    now = datetime.now(timezone.utc)
    try:
        sample = test_provider(provider_id)
        row.last_test_ok = True; row.last_test_message = "连接和翻译测试成功"; row.last_test_at = now
        db.commit()
        quota_manager.clear_status(row.id, row.config or {})
        return ProviderTestOut(ok=True, provider=provider_id, message="连接和翻译测试成功", sample=sample)
    except Exception as exc:
        message = str(exc)[:500]
        low = message.lower()
        if "54004" in low or "please recharge" in low:
            quota_manager.mark(row.id, row.config or {}, "exhausted", message)
        elif "unsynchronized" in low or "servicenotactivated" in low or "invalid sign" in low or "54001" in low:
            quota_manager.mark(row.id, row.config or {}, "unavailable", message)
        row.last_test_ok = False; row.last_test_message = message; row.last_test_at = now
        db.commit()
        return ProviderTestOut(ok=False, provider=provider_id, message=message)


@router.get("/{provider_id}/quota", dependencies=[Depends(require_api_key)])
def get_provider_quota(provider_id: str, db: Session = Depends(get_db)):
    row = db.get(ProviderProfile, provider_id)
    if row is None:
        raise HTTPException(404, "provider not found")
    return quota_manager.snapshot(row.id, row.config or {})


@router.post("/{provider_id}/quota/reset", dependencies=[Depends(require_api_key)])
def reset_provider_quota(provider_id: str, db: Session = Depends(get_db)):
    row = db.get(ProviderProfile, provider_id)
    if row is None:
        raise HTTPException(404, "provider not found")
    return quota_manager.reset_local_usage(row.id, row.config or {})
