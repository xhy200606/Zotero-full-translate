from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import UserProviderProfile, UserTranslationSettings


QUOTA_DEFAULTS = {
    "quota_enabled": True,
    "quota_total_chars": 0,
    "quota_reserve_chars": 0,
    "quota_low_percent": 10,
    "quota_period": "month",
}


def _cfg(**values):
    return {**values, **QUOTA_DEFAULTS}


PROVIDER_CATALOG: dict[str, dict] = {
    "openai_compatible": {
        "kind": "openai_compatible",
        "vendor": "openai",
        "logo": "openai",
        "display_name": "OpenAI Compatible",
        "description": "OpenAI 协议兼容服务",
        "credential_url": "https://platform.openai.com/api-keys",
        "docs_url": "https://platform.openai.com/docs/api-reference",
        "config": _cfg(template_id="openai_compatible", base_url="https://api.openai.com/v1", model="gpt-4.1-mini", qps=2, max_concurrency=2),
    },
    "custom_openai_compatible": {
        "kind": "openai_compatible",
        "vendor": "custom",
        "logo": "custom",
        "display_name": "自定义 OpenAI Compatible",
        "description": "自定义兼容 OpenAI Chat Completions 的 API 地址、模型与密钥",
        "credential_url": None,
        "docs_url": None,
        "config": _cfg(template_id="custom_openai_compatible", base_url="https://api.example.com/v1", model="model-name", qps=2, max_concurrency=2),
    },
    "baidu_general": {
        "kind": "baidu",
        "vendor": "baidu",
        "logo": "baidu",
        "display_name": "百度通用文本翻译",
        "description": "百度通用文本翻译 API",
        "credential_url": "https://fanyi-api.baidu.com/manage/developer",
        "docs_url": "https://fanyi-api.baidu.com/product/113",
        "config": _cfg(template_id="baidu_general", service_type="general", endpoint="https://fanyi-api.baidu.com/api/trans/vip/translate", auth_mode="sign", model_type="nmt", reference="", domain="academic", qps=10, max_concurrency=10),
    },
    "baidu_llm": {
        "kind": "baidu",
        "vendor": "baidu",
        "logo": "baidu",
        "display_name": "百度大模型文本翻译",
        "description": "百度大模型文本翻译 API",
        "credential_url": "https://fanyi-api.baidu.com/manage/apiKey",
        "docs_url": "https://fanyi-api.baidu.com/doc/21",
        "config": _cfg(template_id="baidu_llm", service_type="llm", endpoint="https://fanyi-api.baidu.com/ait/api/aiTextTranslate", auth_mode="api_key", model_type="llm", reference="", domain="academic", qps=10, max_concurrency=10),
    },
    "baidu_domain": {
        "kind": "baidu",
        "vendor": "baidu",
        "logo": "baidu",
        "display_name": "百度领域文本翻译",
        "description": "百度垂直领域文本翻译 API",
        "credential_url": "https://fanyi-api.baidu.com/manage/developer",
        "docs_url": "https://fanyi-api.baidu.com/product/12",
        "config": _cfg(template_id="baidu_domain", service_type="domain", endpoint="https://fanyi-api.baidu.com/api/trans/vip/fieldtranslate", auth_mode="sign", model_type="nmt", reference="", domain="academic", qps=10, max_concurrency=10),
    },
    "tencent_tmt": {
        "kind": "tencent",
        "vendor": "tencent",
        "logo": "tencent",
        "display_name": "腾讯机器翻译 TMT",
        "description": "腾讯云 TMT 文本翻译",
        "credential_url": "https://console.cloud.tencent.com/cam/capi",
        "docs_url": "https://cloud.tencent.com/document/product/551",
        "config": _cfg(template_id="tencent_tmt", auth_mode="tmt_tc3", tmt_endpoint="https://tmt.tencentcloudapi.com", tmt_region="ap-beijing", tmt_version="2018-03-21", project_id=0, max_chars=1900, base_url="https://tokenhub.tencentmaas.com/v1", model="hy-mt2-plus", hunyuan_endpoint="https://hunyuan.ai.tencentcloudapi.com", hunyuan_model="hunyuan-translation-lite", field="学术论文", qps=5, max_concurrency=5),
    },
    "tencent_tokenhub": {
        "kind": "tencent",
        "vendor": "tencent",
        "logo": "tencent",
        "display_name": "腾讯 TokenHub 翻译",
        "description": "腾讯翻译模型 OpenAI-compatible 接口",
        "credential_url": "https://console.cloud.tencent.com/cam/capi",
        "docs_url": "https://cloud.tencent.com/document/product/551",
        "config": _cfg(template_id="tencent_tokenhub", auth_mode="tokenhub", tmt_endpoint="https://tmt.tencentcloudapi.com", tmt_region="ap-beijing", tmt_version="2018-03-21", project_id=0, max_chars=1900, base_url="https://tokenhub.tencentmaas.com/v1", model="hy-mt2-plus", hunyuan_endpoint="https://hunyuan.ai.tencentcloudapi.com", hunyuan_model="hunyuan-translation-lite", field="学术论文", qps=5, max_concurrency=5),
    },
    "tencent_hunyuan": {
        "kind": "tencent",
        "vendor": "tencent",
        "logo": "tencent",
        "display_name": "腾讯混元翻译",
        "description": "腾讯混元 ChatTranslations",
        "credential_url": "https://console.cloud.tencent.com/cam/capi",
        "docs_url": "https://cloud.tencent.com/document/product/1729",
        "config": _cfg(template_id="tencent_hunyuan", auth_mode="hunyuan_tc3", tmt_endpoint="https://tmt.tencentcloudapi.com", tmt_region="ap-beijing", tmt_version="2018-03-21", project_id=0, max_chars=1900, base_url="https://tokenhub.tencentmaas.com/v1", model="hy-mt2-plus", hunyuan_endpoint="https://hunyuan.ai.tencentcloudapi.com", hunyuan_model="hunyuan-translation-lite", field="学术论文", qps=5, max_concurrency=5),
    },
    "volcengine_mt": {
        "kind": "volcengine",
        "vendor": "volcengine",
        "logo": "volcengine",
        "display_name": "火山机器翻译",
        "description": "火山引擎机器翻译",
        "credential_url": "https://console.volcengine.com/iam/keymanage/",
        "docs_url": "https://www.volcengine.com/docs",
        "config": _cfg(template_id="volcengine_mt", endpoint="https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate", resource_id="volc.speech.mt", qps=10, max_concurrency=10),
    },
    "aliyun_general": {
        "kind": "aliyun",
        "vendor": "aliyun",
        "logo": "aliyun",
        "display_name": "阿里机器翻译",
        "description": "阿里云机器翻译通用版",
        "credential_url": "https://ram.console.aliyun.com/manage/ak",
        "docs_url": "https://help.aliyun.com/product/30396.html",
        "config": _cfg(template_id="aliyun_general", endpoint="https://mt.cn-hangzhou.aliyuncs.com", path="/api/translate/web/general", scene="general", max_chars=4900, qps=10, max_concurrency=10),
    },
    "aliyun_professional": {
        "kind": "aliyun",
        "vendor": "aliyun",
        "logo": "aliyun",
        "display_name": "阿里云机器翻译专业版",
        "description": "阿里云机器翻译专业版 Translate 引擎（2018-10-12）",
        "credential_url": "https://ram.console.aliyun.com/manage/ak",
        "docs_url": "https://help.aliyun.com/zh/machine-translation/developer-reference/api-alimt-2018-10-12-translate",
        "config": _cfg(template_id="aliyun_professional", api_mode="rpc", action="Translate", endpoint="https://mt.cn-hangzhou.aliyuncs.com", scene="description", context="", max_chars=4900, qps=50, max_concurrency=10),
    },
}


PROVIDER_TEMPLATES: dict[str, dict] = {
    "openai_compatible": PROVIDER_CATALOG["openai_compatible"],
    "baidu": PROVIDER_CATALOG["baidu_general"],
    "baidu_llm": PROVIDER_CATALOG["baidu_llm"],
    "tencent": PROVIDER_CATALOG["tencent_tmt"],
    "volcengine": PROVIDER_CATALOG["volcengine_mt"],
    "aliyun": PROVIDER_CATALOG["aliyun_general"],
    "aliyun_professional": PROVIDER_CATALOG["aliyun_professional"],
}


def catalog_item(template_id: str | None) -> dict | None:
    if not template_id:
        return None
    row = PROVIDER_CATALOG.get(str(template_id))
    return dict(row) if row else None


def provider_metadata(row: UserProviderProfile) -> dict:
    config = dict(row.config or {})
    template_id = str(config.get("template_id") or "").strip()
    if not template_id:
        if row.provider_id == "baidu":
            service_type = str(config.get("service_type") or "").lower()
            if not service_type:
                mode = str(config.get("auth_mode") or "sign").lower()
                service_type = "llm" if mode == "api_key" else "general"
            template_id = f"baidu_{service_type}"
        elif row.provider_id == "tencent":
            mode = str(config.get("auth_mode") or "tmt_tc3").lower()
            template_id = "tencent_tokenhub" if mode == "tokenhub" else "tencent_hunyuan" if mode == "hunyuan_tc3" else "tencent_tmt"
        elif row.provider_id == "volcengine":
            template_id = "volcengine_mt"
        elif row.provider_id == "aliyun":
            template_id = "aliyun_general"
        elif row.kind == "openai_compatible":
            template_id = "openai_compatible"
    meta = PROVIDER_CATALOG.get(template_id) or {}
    return {
        "template_id": template_id or None,
        "vendor": meta.get("vendor") or row.kind,
        "logo": meta.get("logo") or row.kind,
        "description": meta.get("description") or "",
        "credential_url": meta.get("credential_url"),
        "docs_url": meta.get("docs_url"),
    }


def ensure_user_provider_defaults(db: Session, user_id: str) -> list[UserProviderProfile]:
    rows = list(db.scalars(select(UserProviderProfile).where(UserProviderProfile.user_id == user_id)).all())
    settings = db.get(UserTranslationSettings, user_id)
    changed = False

    baidu_llm_row = next((row for row in rows if row.provider_id == "baidu_llm"), None)
    for row in list(rows):
        cfg = dict(row.config or {})
        is_legacy_machine = (
            row.kind == "baidu"
            and (
                row.provider_id == "baidu_machine"
                or str(cfg.get("template_id") or "").strip() == "baidu_machine"
                or str(cfg.get("service_type") or "").strip().lower() == "machine"
            )
        )
        if not is_legacy_machine:
            continue
        cfg.update({
            "template_id": "baidu_llm",
            "service_type": "llm",
            "auth_mode": "api_key",
            "model_type": "llm",
            "endpoint": "https://fanyi-api.baidu.com/ait/api/aiTextTranslate",
        })
        row.config = cfg
        if "机器翻译" in str(row.display_name or "") or not str(row.display_name or "").strip():
            row.display_name = PROVIDER_CATALOG["baidu_llm"]["display_name"]
        if row.provider_id == "baidu_machine" and baidu_llm_row is None:
            row.provider_id = "baidu_llm"
            baidu_llm_row = row
        changed = True

    if settings is not None:
        pool = []
        for provider_id in list(settings.default_provider_ids or []):
            normalized = "baidu_llm" if provider_id == "baidu_machine" else provider_id
            if normalized not in pool:
                pool.append(normalized)
        if pool != list(settings.default_provider_ids or []):
            settings.default_provider_ids = pool
            changed = True

    existing = {row.provider_id: row for row in rows}
    for provider_id, template in PROVIDER_TEMPLATES.items():
        row = existing.get(provider_id)
        if row is None:
            row = UserProviderProfile(
                user_id=user_id,
                provider_id=provider_id,
                kind=template["kind"],
                display_name=template["display_name"],
                enabled=False,
                config=dict(template["config"]),
                secret_payload=None,
            )
            db.add(row)
            existing[provider_id] = row
            rows.append(row)
            changed = True
        else:
            cfg = dict(row.config or {})
            if provider_id == "baidu" and not cfg.get("service_type"):
                mode = str(cfg.get("auth_mode") or "sign").lower()
                service = "llm" if mode == "api_key" else "general"
                cfg["service_type"] = service
                cfg["template_id"] = f"baidu_{service}"
                if service == "llm":
                    cfg["model_type"] = "llm"
                row.config = cfg
                if row.display_name == "百度翻译":
                    row.display_name = PROVIDER_CATALOG[f"baidu_{service}"]["display_name"]
                changed = True
            elif not cfg.get("template_id"):
                meta = provider_metadata(row)
                if meta.get("template_id"):
                    cfg["template_id"] = meta["template_id"]
                    row.config = cfg
                    changed = True

    if settings is None:
        settings = UserTranslationSettings(user_id=user_id, default_provider_ids=[], default_provider_strategy="balanced")
        db.add(settings)
        changed = True
    if changed:
        db.commit()
    ordered = [existing[x] for x in PROVIDER_TEMPLATES if x in existing]
    custom = sorted((row for key, row in existing.items() if key not in PROVIDER_TEMPLATES), key=lambda row: (row.display_name.lower(), row.provider_id))
    return ordered + custom


def get_user_translation_settings(db: Session, user_id: str) -> UserTranslationSettings:
    ensure_user_provider_defaults(db, user_id)
    row = db.get(UserTranslationSettings, user_id)
    if row is None:
        row = UserTranslationSettings(user_id=user_id, default_provider_ids=[], default_provider_strategy="balanced")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def touch_user_translation_settings(row: UserTranslationSettings) -> None:
    row.updated_at = datetime.now(timezone.utc)
