from __future__ import annotations

from .config import get_settings
from .crypto import decrypt_json, encrypt_json
from .db import SessionLocal
from .models import ProviderProfile, RuntimeConfig


def _secret(payload: dict) -> str | None:
    clean = {k: v for k, v in payload.items() if v}
    return encrypt_json(clean) if clean else None


def ensure_runtime_defaults() -> None:
    s = get_settings()
    with SessionLocal() as db:
        runtime = db.get(RuntimeConfig, 1)
        if runtime is None:
            runtime = RuntimeConfig(
                id=1,
                default_provider=s.zft_translator_provider,
                default_provider_ids=[s.zft_translator_provider],
                default_provider_strategy="balanced",
                max_active_jobs=max(1, s.zft_max_active_jobs),
                babeldoc_qps=max(1, s.babeldoc_qps),
                pool_max_workers=max(1, s.babeldoc_pool_max_workers),
                multi_pool_max_workers=max(1, s.babeldoc_multi_pool_max_workers),
                aggregate_qps_cap=max(1, s.babeldoc_aggregate_qps_cap),
                report_interval=max(0.1, s.babeldoc_report_interval),
                max_pages_per_part=max(1, s.babeldoc_max_pages_per_part),
                skip_scanned_detection=s.babeldoc_skip_scanned_detection,
                auto_ocr_workaround=s.babeldoc_auto_ocr_workaround,
            )
            db.add(runtime)
        else:
            if not getattr(runtime, "default_provider_ids", None):
                runtime.default_provider_ids = [runtime.default_provider]
            if not getattr(runtime, "default_provider_strategy", None):
                runtime.default_provider_strategy = "balanced"
            if getattr(runtime, "multi_pool_max_workers", None) is None:
                runtime.multi_pool_max_workers = 12
            if getattr(runtime, "aggregate_qps_cap", None) is None:
                runtime.aggregate_qps_cap = 100
            if getattr(runtime, "quota_aware_dispatch", None) is None:
                runtime.quota_aware_dispatch = True

        if db.get(ProviderProfile, "openai_compatible") is None:
            db.add(ProviderProfile(
                id="openai_compatible", kind="openai_compatible", display_name="OpenAI Compatible",
                enabled=bool(s.openai_api_key),
                config={"base_url": s.openai_base_url, "model": s.openai_model, "qps": 2, "max_concurrency": 2, "quota_enabled": True, "quota_total_chars": 0, "quota_reserve_chars": 0, "quota_low_percent": 10, "quota_period": "month"},
                secret_payload=_secret({"api_key": s.openai_api_key}),
            ))

        if db.get(ProviderProfile, "baidu") is None:
            db.add(ProviderProfile(
                id="baidu", kind="baidu", display_name="百度翻译",
                enabled=bool(s.baidu_app_id and s.baidu_secret_key),
                config={"endpoint": s.baidu_endpoint, "auth_mode": "sign", "model_type": "nmt", "reference": "", "qps": 10, "max_concurrency": 10, "quota_enabled": True, "quota_total_chars": 0, "quota_reserve_chars": 0, "quota_low_percent": 10, "quota_period": "month"},
                secret_payload=_secret({"app_id": s.baidu_app_id, "secret_key": s.baidu_secret_key}),
            ))

        baidu = db.get(ProviderProfile, "baidu")
        if baidu is not None:
            cfg = dict(baidu.config or {})
            # v1.3 shipped Baidu at 1 QPS. Raise only that legacy/default value;
            # preserve explicit user tuning above 1 QPS.
            if float(cfg.get("qps") or 1) <= 1.0:
                cfg["qps"] = 10
            if int(cfg.get("max_concurrency") or 1) <= 1:
                cfg["max_concurrency"] = 10
            cfg.setdefault("quota_enabled", True)
            cfg.setdefault("quota_total_chars", 0)
            cfg.setdefault("quota_reserve_chars", 0)
            cfg.setdefault("quota_low_percent", 10)
            cfg.setdefault("quota_period", "month")
            baidu.config = cfg

        tencent = db.get(ProviderProfile, "tencent")
        if tencent is None:
            # Prefer the user's Tencent Machine Translation (TMT) entitlement when
            # SecretId/SecretKey are supplied. TokenHub remains an explicit option.
            initial_mode = "tokenhub" if s.tencent_tokenhub_api_key and not (s.tencent_secret_id and s.tencent_secret_key) else "tmt_tc3"
            db.add(ProviderProfile(
                id="tencent", kind="tencent", display_name="腾讯机器翻译 TMT",
                enabled=bool(s.tencent_tokenhub_api_key or (s.tencent_secret_id and s.tencent_secret_key)),
                config={
                    "auth_mode": initial_mode,
                    "tmt_endpoint": "https://tmt.tencentcloudapi.com",
                    "tmt_region": "ap-beijing",
                    "tmt_version": "2018-03-21",
                    "project_id": 0,
                    "max_chars": 1900,
                    "base_url": "https://tokenhub.tencentmaas.com/v1",
                    "model": "hy-mt2-plus",
                    "hunyuan_endpoint": "https://hunyuan.ai.tencentcloudapi.com",
                    "hunyuan_model": "hunyuan-translation-lite",
                    "field": "学术论文",
                    "qps": 5, "max_concurrency": 5,
                    "quota_enabled": True, "quota_total_chars": 0, "quota_reserve_chars": 0, "quota_low_percent": 10, "quota_period": "month",
                },
                secret_payload=_secret({
                    "api_key": s.tencent_tokenhub_api_key,
                    "secret_id": s.tencent_secret_id,
                    "secret_key": s.tencent_secret_key,
                }),
            ))
        else:
            # v1.3.0 used legacy_tc3 to mean Hunyuan. That caused TMT users to
            # receive FailedOperation.ServiceNotActivated. Migrate only that old
            # compatibility value; explicit TokenHub settings are left untouched.
            cfg = dict(tencent.config or {})
            mode = str(cfg.get("auth_mode") or "").lower()
            if mode == "legacy_tc3":
                cfg["auth_mode"] = "tmt_tc3"
            cfg.setdefault("tmt_endpoint", "https://tmt.tencentcloudapi.com")
            cfg.setdefault("tmt_region", "ap-beijing")
            cfg.setdefault("tmt_version", "2018-03-21")
            cfg.setdefault("project_id", 0)
            cfg.setdefault("max_chars", 1900)
            cfg.setdefault("hunyuan_endpoint", cfg.pop("legacy_endpoint", "https://hunyuan.ai.tencentcloudapi.com"))
            cfg.setdefault("hunyuan_model", cfg.pop("legacy_model", "hunyuan-translation-lite"))
            cfg.setdefault("qps", 5)
            cfg.setdefault("max_concurrency", 5)
            cfg.setdefault("quota_enabled", True)
            cfg.setdefault("quota_total_chars", 0)
            cfg.setdefault("quota_reserve_chars", 0)
            cfg.setdefault("quota_low_percent", 10)
            cfg.setdefault("quota_period", "month")
            tencent.config = cfg
            if cfg.get("auth_mode") == "tmt_tc3":
                tencent.display_name = "腾讯机器翻译 TMT"

        volcengine = db.get(ProviderProfile, "volcengine")
        if volcengine is None:
            db.add(ProviderProfile(
                id="volcengine", kind="volcengine", display_name="火山机器翻译",
                enabled=bool(s.volc_api_key),
                config={
                    "endpoint": "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate",
                    "resource_id": "volc.speech.mt",
                    "qps": 10, "max_concurrency": 10,
                    "quota_enabled": True, "quota_total_chars": 0, "quota_reserve_chars": 0, "quota_low_percent": 10, "quota_period": "month",
                },
                secret_payload=_secret({"api_key": s.volc_api_key}),
            ))
        else:
            cfg = dict(volcengine.config or {})
            # v1.4.1 switches Volcengine to the API-key based Machine Translation endpoint.
            # Remove stale TC3/OpenAPI routing values so the Web UI exposes only the active API.
            for key in ("api_mode", "region", "service", "version", "legacy_endpoint", "legacy_region", "legacy_version"):
                cfg.pop(key, None)
            cfg["endpoint"] = "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate"
            cfg["resource_id"] = "volc.speech.mt"
            if float(cfg.get("qps") or 5) <= 5.0:
                cfg["qps"] = 10
            if int(cfg.get("max_concurrency") or 5) <= 5:
                cfg["max_concurrency"] = 10
            cfg.setdefault("quota_enabled", True)
            cfg.setdefault("quota_total_chars", 0)
            cfg.setdefault("quota_reserve_chars", 0)
            cfg.setdefault("quota_low_percent", 10)
            cfg.setdefault("quota_period", "month")
            volcengine.config = cfg
            # Old AK/SK values are left encrypted for rollback, but only api_key is used from v1.4.1 onward.
            secrets = decrypt_json(volcengine.secret_payload)
            if s.volc_api_key and not secrets.get("api_key"):
                secrets["api_key"] = s.volc_api_key
                volcengine.secret_payload = _secret(secrets)
            if not secrets.get("api_key"):
                # A v1.4.0 profile may still be marked enabled with only obsolete AK/SK.
                # Disable it until the new API Key is saved so default multi-engine jobs remain runnable.
                volcengine.enabled = False

        if db.get(ProviderProfile, "aliyun") is None:
            db.add(ProviderProfile(
                id="aliyun", kind="aliyun", display_name="阿里机器翻译",
                enabled=bool(s.aliyun_access_key_id and s.aliyun_access_key_secret),
                config={
                    "endpoint": "https://mt.cn-hangzhou.aliyuncs.com", "path": "/api/translate/web/general",
                    "scene": "general", "max_chars": 4900, "qps": 10, "max_concurrency": 10,
                    "quota_enabled": True, "quota_total_chars": 0, "quota_reserve_chars": 0, "quota_low_percent": 10, "quota_period": "month",
                },
                secret_payload=_secret({"access_key_id": s.aliyun_access_key_id, "access_key_secret": s.aliyun_access_key_secret}),
            ))

        aliyun = db.get(ProviderProfile, "aliyun")
        if aliyun is not None:
            cfg = dict(aliyun.config or {})
            cfg.setdefault("quota_enabled", True)
            cfg.setdefault("quota_total_chars", 0)
            cfg.setdefault("quota_reserve_chars", 0)
            cfg.setdefault("quota_low_percent", 10)
            cfg.setdefault("quota_period", "month")
            aliyun.config = cfg
        openai_profile = db.get(ProviderProfile, "openai_compatible")
        if openai_profile is not None:
            cfg = dict(openai_profile.config or {})
            cfg.setdefault("quota_enabled", True)
            cfg.setdefault("quota_total_chars", 0)
            cfg.setdefault("quota_reserve_chars", 0)
            cfg.setdefault("quota_low_percent", 10)
            cfg.setdefault("quota_period", "month")
            openai_profile.config = cfg

        # Keep a fresh/updated server usable even when the configured legacy default
        # provider has no credentials. Do not silently enable anything; just select
        # among providers that are already enabled.
        db.flush()
        enabled_rows = db.query(ProviderProfile).filter(ProviderProfile.enabled.is_(True)).order_by(ProviderProfile.id.asc()).all()
        enabled_ids = [x.id for x in enabled_rows]
        current_pool = [x for x in list(getattr(runtime, "default_provider_ids", None) or []) if x in enabled_ids]
        if not current_pool and runtime.default_provider in enabled_ids:
            current_pool = [runtime.default_provider]
        if not current_pool and enabled_ids:
            current_pool = [enabled_ids[0]]
        if current_pool:
            runtime.default_provider_ids = current_pool
            runtime.default_provider = current_pool[0]
        db.commit()
