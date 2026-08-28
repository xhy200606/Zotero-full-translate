from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
import openai
from babeldoc.translator.translator import BaseTranslator

from ..crypto import decrypt_json
from ..db import SessionLocal
from ..models import ProviderProfile
from .rate_limit import gate
from .quota import quota_manager
from .translation_memory import translation_memory


def _lang(code: str, *, auto: str = "auto") -> str:
    value = (code or "").strip().lower()
    mapping = {
        "zh-cn": "zh", "zh-hans": "zh", "zh_hans": "zh", "zh-cn-simp": "zh",
        "zh-tw": "zh-TR", "zh-hant": "zh-TR",
        "en-us": "en", "en-gb": "en",
    }
    if value in {"", "auto"}:
        return auto
    return mapping.get(value, value)


def _tencent_lang(code: str, *, auto: str = "auto") -> str:
    value = (code or "").strip().lower()
    mapping = {
        "zh-cn": "zh", "zh-hans": "zh", "zh_hans": "zh",
        "zh-tw": "zh-TW", "zh-hant": "zh-TW", "zh_hant": "zh-TW",
        "en-us": "en", "en-gb": "en",
    }
    if value in {"", "auto"}:
        return auto
    return mapping.get(value, value)


def _sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes | str, data: bytes | str) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).digest()


def _provider_limits(row: ProviderProfile, fallback_qps: int = 1) -> tuple[float, int]:
    cfg = row.config or {}
    qps = float(cfg.get("qps") or fallback_qps or 1)
    concurrency = int(cfg.get("max_concurrency") or max(1, round(qps)))
    return max(0.1, qps), max(1, concurrency)


class BaiduTranslator(BaseTranslator):
    name = "baidu"
    lang_map = {"zh-cn": "zh", "zh-hans": "zh", "en-us": "en"}

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, app_id: str, endpoint: str, *, auth_mode: str = "sign",
                 secret_key: str = "", api_key: str = "", model_type: str = "llm",
                 reference: str = "", ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.app_id = app_id.strip()
        self.secret_key = secret_key.strip()
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        self.auth_mode = (auth_mode or "sign").strip().lower()
        self.model_type = (model_type or "llm").strip().lower()
        self.reference = (reference or "").strip()
        self.model = "baidu-ai-text" if self.auth_mode == "api_key" else "baidu-general"
        self.client = httpx.Client(timeout=120)
        self.add_cache_impact_parameters("provider", self.model)
        self.add_cache_impact_parameters("auth_mode", self.auth_mode)
        self.add_cache_impact_parameters("model_type", self.model_type)

    def _request_payload(self, text: str) -> tuple[dict[str, str], dict[str, str]]:
        if self.auth_mode == "api_key":
            if not self.app_id or not self.api_key:
                raise RuntimeError("百度大模型翻译需要 APPID 和 API Key")
            body: dict[str, str] = {
                "appid": self.app_id, "q": text, "from": _lang(self.lang_in),
                "to": _lang(self.lang_out), "model_type": self.model_type if self.model_type in {"llm", "nmt"} else "llm",
            }
            if self.reference:
                body["reference"] = self.reference
            return body, {"Authorization": f"Bearer {self.api_key}"}
        if not self.app_id or not self.secret_key:
            raise RuntimeError("百度通用翻译需要 APPID 和开发者信息中的密钥")
        salt = str(random.randint(100000, 999999999))
        sign = hashlib.md5(f"{self.app_id}{text}{salt}{self.secret_key}".encode("utf-8")).hexdigest()
        return {"q": text, "from": _lang(self.lang_in), "to": _lang(self.lang_out), "appid": self.app_id, "salt": salt, "sign": sign}, {}

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        for attempt in range(6):
            try:
                with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
                    gate.record_request(self.provider_id, chars=len(text), wait_ms=wait_ms)
                    body, headers = self._request_payload(text)
                    if self.auth_mode == "api_key":
                        response = self.client.post(self.endpoint, json=body, headers=headers)
                    else:
                        response = self.client.post(self.endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
                response.raise_for_status()
                payload = response.json()
                code = str(payload.get("error_code", ""))
                if code in {"54003", "54005", "59004"}:
                    gate.record_error(self.provider_id, f"Baidu rate limit {code}")
                    time.sleep(min(20.0, 1.5 * (2 ** attempt)))
                    continue
                if code:
                    raw_message = str(payload.get("error_msg", "unknown error"))
                    if code == "54001":
                        hint = ("请检查 API Key 是否来自百度翻译开放平台的“API Key管理”，并确认 APPID 与该账号一致"
                                if self.auth_mode == "api_key" else
                                "请填写百度翻译开放平台“开发者信息”中的 APPID 与密钥；不要把“API Key管理”生成的 API Key 填到密钥字段")
                        message = f"Baidu error 54001: {raw_message}。{hint}"
                    else:
                        message = f"Baidu error {code}: {raw_message}"
                    gate.record_error(self.provider_id, message)
                    # 54004 = account balance/quota exhausted. It is a hard
                    # account state, not a transient rate limit, so return it to
                    # MultiProviderTranslator immediately for quota-aware failover.
                    if code in {"52003", "54000", "54001", "54004", "58000", "58001", "58002", "58003", "58004"}:
                        raise ValueError(message)
                    raise RuntimeError(message)
                rows = payload.get("trans_result") or []
                return "\n".join(str(row.get("dst", "")) for row in rows).strip()
            except ValueError:
                raise
            except Exception as exc:
                gate.record_error(self.provider_id, str(exc))
                if attempt >= 5:
                    raise
                time.sleep(min(12.0, 1.0 * (2 ** attempt)))
        raise RuntimeError("Baidu rate limit persisted after retries")

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError


class CloudOpenAITranslator(BaseTranslator):
    name = "zft-openai"

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, model: str, base_url: str, api_key: str,
                 ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.model = model
        self.base_url = base_url
        self.client = openai.OpenAI(
            base_url=base_url, api_key=api_key,
            http_client=httpx.Client(limits=httpx.Limits(max_connections=None, max_keepalive_connections=None), timeout=600),
        )
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("base_url", self.base_url)
        self.add_cache_impact_parameters("temperature", 0)

    def _request(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        last: Exception | None = None
        for attempt in range(6):
            chars = sum(len(str(x.get("content", ""))) for x in messages)
            try:
                with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
                    gate.record_request(self.provider_id, chars=chars, wait_ms=wait_ms)
                    kwargs: dict[str, Any] = {"model": self.model, "temperature": 0, "messages": messages, "max_tokens": 2048}
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise RuntimeError("translation provider returned an empty response")
                return content.strip()
            except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError) as exc:
                last = exc
                gate.record_error(self.provider_id, f"{exc.__class__.__name__}: {exc}")
                time.sleep(min(20.0, 1.25 * (2 ** attempt)))
            except Exception as exc:
                gate.record_error(self.provider_id, str(exc))
                raise
        raise last or RuntimeError("OpenAI-compatible translation failed after retries")

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        return self._request([
            {"role": "system", "content": "You are a professional, authentic machine translation engine."},
            {"role": "user", "content": f"Treat the following as plain text and translate it into {self.lang_out}. Output translation only. Preserve placeholders, formulas, codes and proper nouns when translation is unnecessary.\n\n{text}"},
        ])

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        if text is None:
            return None
        return self._request([{"role": "user", "content": text}], json_mode=bool((rate_limit_params or {}).get("request_json_mode")))

    def get_formular_placeholder(self, placeholder_id: int | str):
        return "{v" + str(placeholder_id) + "}", f"{{\\s*v\\s*{placeholder_id}\\s*}}"

    def get_rich_text_left_placeholder(self, placeholder_id: int | str):
        return f"<style id='{placeholder_id}'>", f"<\\s*style\\s*id\\s*=\\s*'\\s*{placeholder_id}\\s*'\\s*>"

    def get_rich_text_right_placeholder(self, placeholder_id: int | str):
        return "</style>", r"<\s*\/\s*style\s*>"


class TencentTokenHubTranslator(CloudOpenAITranslator):
    """Tencent TokenHub Hy-MT2 via the current OpenAI-compatible API."""
    name = "zft-tencent"

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        return self._request([
            {
                "role": "user",
                "content": (
                    f"Translate the following text into {self.lang_out}. Output translation only. "
                    "Preserve placeholders, formulas, citation markers, variable names and codes exactly.\n\n"
                    f"{text}"
                ),
            }
        ])


class TencentTMTTranslator(BaseTranslator):
    """Tencent Machine Translation TextTranslate (API 3.0 compatibility path).

    Tencent removed TextTranslate from the current TMT API overview in July 2026,
    but existing TMT accounts may still have access to the historical 2018-03-21
    action. This adapter is intentionally kept for those accounts instead of
    silently routing them to Hunyuan, which is a different product entitlement.
    """
    name = "zft-tencent"

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, endpoint: str, secret_id: str, secret_key: str,
                 region: str = "ap-beijing", version: str = "2018-03-21",
                 project_id: int = 0, max_chars: int = 1900, ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.endpoint = endpoint.rstrip("/") + "/"
        self.secret_id = secret_id.strip()
        self.secret_key = secret_key.strip()
        self.region = region.strip() or "ap-beijing"
        self.version = version.strip() or "2018-03-21"
        self.project_id = int(project_id or 0)
        self.max_chars = max(200, min(1950, int(max_chars or 1900)))
        self.model = "tencent-tmt-texttranslate"
        self.client = httpx.Client(timeout=120)
        self.add_cache_impact_parameters("endpoint", self.endpoint)
        self.add_cache_impact_parameters("version", self.version)
        self.add_cache_impact_parameters("region", self.region)

    def _headers(self, payload: str) -> dict[str, str]:
        parsed = urlparse(self.endpoint)
        host = parsed.netloc
        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        content_type = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
        signed_headers = "content-type;host"
        canonical_request = "\n".join([
            "POST", parsed.path or "/", "", canonical_headers,
            signed_headers, _sha256_hex(payload),
        ])
        scope = f"{date}/tmt/tc3_request"
        string_to_sign = "\n".join([
            "TC3-HMAC-SHA256", str(timestamp), scope, _sha256_hex(canonical_request),
        ])
        secret_date = _hmac_sha256("TC3" + self.secret_key, date)
        secret_service = _hmac_sha256(secret_date, "tmt")
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": host,
            "X-TC-Action": "TextTranslate",
            "X-TC-Version": self.version,
            "X-TC-Region": self.region,
            "X-TC-Timestamp": str(timestamp),
        }

    def _one(self, text: str) -> str:
        body = {
            "SourceText": text,
            "Source": _tencent_lang(self.lang_in),
            "Target": _tencent_lang(self.lang_out),
            "ProjectId": self.project_id,
        }
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
            gate.record_request(self.provider_id, chars=len(text), wait_ms=wait_ms)
            resp = self.client.post(
                self.endpoint,
                content=payload.encode("utf-8"),
                headers=self._headers(payload),
            )
        try:
            data = resp.json()
        except Exception:
            data = {}
        response = data.get("Response") if isinstance(data, dict) else None
        response = response if isinstance(response, dict) else {}
        error = response.get("Error")
        if resp.status_code >= 400 or error:
            code = str((error or {}).get("Code") or f"HTTP{resp.status_code}")
            msg = str((error or {}).get("Message") or resp.text[:300] or "unknown error")
            if "ServiceNotActivated" in code or "UserNotRegistered" in code:
                msg += "；这是腾讯机器翻译 TMT 服务，请在 TMT 控制台确认文本翻译已开通"
            if "ActionNotFound" in code or "UnsupportedOperation" in code:
                msg += "；腾讯已在 2026-07 从当前 TMT API 概览移除 TextTranslate，新账号可能无法再调用该历史接口，可切换 TokenHub"
            raise RuntimeError(f"Tencent TMT {code}: {msg}")
        translated = str(response.get("TargetText") or "").strip()
        if not translated:
            raise RuntimeError("Tencent TMT returned an empty translation")
        return translated

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        chunks: list[str] = []
        rest = text
        while len(rest) > self.max_chars:
            cut = max(
                rest.rfind("\n", 0, self.max_chars),
                rest.rfind(". ", 0, self.max_chars),
                rest.rfind("。", 0, self.max_chars),
                rest.rfind(" ", 0, self.max_chars),
            )
            if cut < self.max_chars // 2:
                cut = self.max_chars
            take = cut + (1 if rest[cut:cut + 1] == "。" else 0)
            chunks.append(rest[:take])
            rest = rest[take:]
        if rest:
            chunks.append(rest)
        out: list[str] = []
        for chunk in chunks:
            last: Exception | None = None
            for attempt in range(4):
                try:
                    out.append(self._one(chunk))
                    last = None
                    break
                except Exception as exc:
                    last = exc
                    message = str(exc)
                    gate.record_error(self.provider_id, message)
                    if any(x in message for x in ["ServiceNotActivated", "UserNotRegistered", "ActionNotFound", "UnsupportedOperation", "AuthFailure", "InvalidCredential"]):
                        raise
                    if attempt >= 3:
                        raise
                    time.sleep(min(8.0, 1.0 * (2 ** attempt)))
            if last is not None:
                raise last
        return "".join(out)

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError


class TencentHunyuanTranslator(BaseTranslator):
    """Tencent Hunyuan ChatTranslations using TC3-HMAC-SHA256.

    Kept as an explicit optional mode. It is NOT interchangeable with Tencent TMT;
    a TMT subscription does not imply Hunyuan ChatTranslations entitlement.
    """
    name = "zft-tencent"

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, endpoint: str, secret_id: str, secret_key: str,
                 model: str = "hunyuan-translation-lite", field: str = "", ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.endpoint = endpoint.rstrip("/") + "/"
        self.secret_id = secret_id.strip()
        self.secret_key = secret_key.strip()
        self.model = model.strip() or "hunyuan-translation-lite"
        self.field = field.strip()
        self.client = httpx.Client(timeout=180)
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("field", self.field)

    def _headers(self, payload: str) -> dict[str, str]:
        parsed = urlparse(self.endpoint)
        host = parsed.netloc
        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        content_type = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
        signed_headers = "content-type;host"
        canonical_request = "\n".join(["POST", parsed.path or "/", "", canonical_headers, signed_headers, _sha256_hex(payload)])
        scope = f"{date}/hunyuan/tc3_request"
        string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope, _sha256_hex(canonical_request)])
        secret_date = _hmac_sha256("TC3" + self.secret_key, date)
        secret_service = _hmac_sha256(secret_date, "hunyuan")
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = f"TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        return {
            "Authorization": authorization, "Content-Type": content_type,
            "Host": host, "X-TC-Action": "ChatTranslations", "X-TC-Version": "2023-09-01",
            "X-TC-Timestamp": str(timestamp),
        }

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        body: dict[str, Any] = {
            "Model": self.model, "Stream": False, "Text": text,
            "Source": _lang(self.lang_in), "Target": _lang(self.lang_out),
        }
        if self.field:
            body["Field"] = self.field
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(5):
            try:
                with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
                    gate.record_request(self.provider_id, chars=len(text), wait_ms=wait_ms)
                    resp = self.client.post(self.endpoint, content=payload.encode("utf-8"), headers=self._headers(payload))
                resp.raise_for_status()
                data = resp.json().get("Response") or {}
                if data.get("Error"):
                    err = data["Error"]
                    code = str(err.get("Code") or "")
                    msg = str(err.get("Message") or "unknown error")
                    message = f"Tencent Hunyuan {code}: {msg}"
                    gate.record_error(self.provider_id, message)
                    if "ServiceNotActivated" in code:
                        raise RuntimeError(message + "；混元与腾讯机器翻译 TMT 是不同服务，请改选 TMT 模式或单独开通混元翻译")
                    if "Limit" in code or "RequestLimit" in code or "RateLimit" in code:
                        time.sleep(min(12.0, 1.5 * (2 ** attempt)))
                        continue
                    raise RuntimeError(message)
                choices = data.get("Choices") or []
                content = (((choices[0] if choices else {}).get("Message") or {}).get("Content"))
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Tencent Hunyuan returned an empty translation")
                return content.strip()
            except Exception as exc:
                gate.record_error(self.provider_id, str(exc))
                if "ServiceNotActivated" in str(exc) or attempt >= 4:
                    raise
                time.sleep(min(10.0, 1.0 * (2 ** attempt)))
        raise RuntimeError("Tencent Hunyuan translation failed")

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError


class VolcengineTranslator(BaseTranslator):
    name = "zft-volc"

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, endpoint: str, api_key: str,
                 resource_id: str = "volc.speech.mt", ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.endpoint = endpoint.strip() or "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate"
        self.api_key = api_key.strip()
        self.resource_id = resource_id.strip() or "volc.speech.mt"
        self.model = "volc-speech-mt"
        self.client = httpx.Client(timeout=120)
        self.add_cache_impact_parameters("endpoint", self.endpoint)
        self.add_cache_impact_parameters("resource_id", self.resource_id)

    @staticmethod
    def _ci_get(payload: dict, *names: str):
        wanted = {str(x).lower().replace("_", "") for x in names}
        for key, value in payload.items():
            if str(key).lower().replace("_", "") in wanted:
                return value
        return None

    @staticmethod
    def _extract_translation(payload: Any) -> str:
        """Read translation text from common openspeech response envelopes."""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            values = [VolcengineTranslator._extract_translation(x) for x in payload]
            return "\n".join(x for x in values if x).strip()
        if not isinstance(payload, dict):
            return ""

        value = VolcengineTranslator._ci_get(
            payload, "translation", "translated_text", "translatedText", "target_text", "targetText"
        )
        if isinstance(value, str) and value.strip():
            return value.strip()

        value = VolcengineTranslator._ci_get(
            payload, "translation_list", "translationList", "translations", "text_list", "textList", "texts"
        )
        if isinstance(value, list) and value:
            translated = [VolcengineTranslator._extract_translation(x) for x in value]
            result = "\n".join(x for x in translated if x).strip()
            if result:
                return result

        for name in ("data", "result", "response", "output"):
            value = VolcengineTranslator._ci_get(payload, name)
            translated = VolcengineTranslator._extract_translation(value)
            if translated:
                return translated
        return ""

    @staticmethod
    def _error_message(resp: httpx.Response, payload: Any) -> str:
        # openspeech v3 APIs report the application status in response headers.
        header_code = str(resp.headers.get("X-Api-Status-Code") or "").strip()
        header_message = str(
            resp.headers.get("X-Api-Message")
            or resp.headers.get("X-Api-Status-Message")
            or ""
        ).strip()
        if header_code:
            if header_code == "20000000":
                return ""
            return f"Volcengine {header_code}: {header_message or resp.text[:400] or 'translation failed'}"

        if isinstance(payload, dict):
            code = VolcengineTranslator._ci_get(payload, "code", "status_code", "statusCode")
            message = VolcengineTranslator._ci_get(payload, "message", "msg", "error_message", "errorMessage")
            err = VolcengineTranslator._ci_get(payload, "error")
            if isinstance(err, dict):
                code = VolcengineTranslator._ci_get(err, "code", "status_code", "statusCode") or code
                message = VolcengineTranslator._ci_get(err, "message", "msg", "error_message", "errorMessage") or message
            success_codes = {None, 0, "0", 20000000, "20000000", "OK", "ok", "success", "Success"}
            if code not in success_codes or resp.status_code >= 400:
                return f"Volcengine {code if code not in (None, '') else resp.status_code}: {message or resp.text[:400] or 'translation failed'}"
        if resp.status_code >= 400:
            return f"Volcengine HTTP {resp.status_code}: {resp.text[:400] or 'translation failed'}"
        return ""

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        if not self.api_key:
            raise RuntimeError("火山机器翻译需要 API Key")
        source = _lang(self.lang_in)
        target = _lang(self.lang_out)
        payload = {
            "source_language": source,
            "target_language": target,
            "text_list": [text],
        }
        for attempt in range(5):
            request_id = str(uuid.uuid4())
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "X-Api-Resource-Id": self.resource_id,
                "X-Api-Request-Id": request_id,
            }
            try:
                with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
                    gate.record_request(self.provider_id, chars=len(text), wait_ms=wait_ms)
                    resp = self.client.post(self.endpoint, json=payload, headers=headers)
                try:
                    data = resp.json()
                except Exception:
                    data = None
                error = self._error_message(resp, data)
                if error:
                    gate.record_error(self.provider_id, error)
                    low = error.lower()
                    # Credential/resource errors cannot recover through retries.
                    if any(x in low for x in ["401", "403", "invalid", "unauthorized", "forbidden", "resource", "api key", "apikey", "45000001"]):
                        raise ValueError(error)
                    if "429" in low or "too many" in low or "rate" in low or "55000031" in low:
                        time.sleep(min(12.0, 1.5 * (2 ** attempt)))
                        continue
                    raise RuntimeError(error)
                translated = self._extract_translation(data)
                if not translated:
                    status_code = str(resp.headers.get("X-Api-Status-Code") or "")
                    raise RuntimeError(
                        f"Volcengine returned an empty translation (request_id={request_id}, status={status_code or resp.status_code})"
                    )
                return translated
            except ValueError:
                raise
            except Exception as exc:
                gate.record_error(self.provider_id, str(exc))
                if attempt >= 4:
                    raise
                time.sleep(min(8.0, 1.0 * (2 ** attempt)))
        raise RuntimeError("Volcengine translation failed")

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError


class AliyunTranslator(BaseTranslator):
    name = "zft-aliyun"

    def __init__(self, lang_in: str, lang_out: str, provider_id: str, qps: float,
                 max_concurrency: int, endpoint: str, access_key_id: str, access_key_secret: str,
                 path: str = "/api/translate/web/general", scene: str = "general",
                 max_chars: int = 4900, ignore_cache: bool = False):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.provider_id = provider_id
        self.qps = max(0.1, float(qps))
        self.max_concurrency = max(1, int(max_concurrency))
        self.endpoint = endpoint.rstrip("/")
        self.access_key_id = access_key_id.strip()
        self.access_key_secret = access_key_secret.strip()
        self.path = path if path.startswith("/") else "/" + path
        self.scene = scene or "general"
        self.max_chars = max(500, int(max_chars))
        self.model = "aliyun-general"
        self.client = httpx.Client(timeout=120)
        self.add_cache_impact_parameters("path", self.path)
        self.add_cache_impact_parameters("scene", self.scene)

    def _headers(self, payload: bytes) -> dict[str, str]:
        parsed = urlparse(self.endpoint)
        host = parsed.netloc
        content_type = "application/json;charset=utf-8"
        accept = "application/json"
        content_md5 = base64.b64encode(hashlib.md5(payload).digest()).decode("ascii")
        date_value = format_datetime(datetime.now(timezone.utc), usegmt=True)
        nonce = uuid.uuid4().hex
        acs_headers = (
            "x-acs-signature-method:HMAC-SHA1\n"
            f"x-acs-signature-nonce:{nonce}\n"
            "x-acs-version:2019-01-02\n"
        )
        string_to_sign = "\n".join(["POST", accept, content_md5, content_type, date_value]) + "\n" + acs_headers + self.path
        signature = base64.b64encode(hmac.new(self.access_key_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")
        return {
            "Authorization": f"acs {self.access_key_id}:{signature}", "Content-Type": content_type,
            "Content-MD5": content_md5, "Date": date_value, "Accept": accept, "Host": host,
            "x-acs-signature-nonce": nonce, "x-acs-signature-method": "HMAC-SHA1", "x-acs-version": "2019-01-02",
        }

    def _one(self, text: str) -> str:
        body = {"FormatType": "text", "SourceLanguage": _lang(self.lang_in), "TargetLanguage": _lang(self.lang_out), "SourceText": text, "Scene": self.scene}
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url = self.endpoint + self.path
        with gate.slot(self.provider_id, self.qps, self.max_concurrency) as wait_ms:
            gate.record_request(self.provider_id, chars=len(text), wait_ms=wait_ms)
            resp = self.client.post(url, content=payload, headers=self._headers(payload))
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Aliyun authentication/API error: {resp.text[:300]}")
        root = data.get("TranslateGeneralResponse") if isinstance(data, dict) else None
        root = root if isinstance(root, dict) else data
        code = str((root or {}).get("Code") or "200")
        if code not in {"200", "Success", "success"}:
            raise RuntimeError(f"Aliyun {code}: {(root or {}).get('Message') or 'translation failed'}")
        translated = str((((root or {}).get("Data") or {}).get("Translated")) or "").strip()
        if not translated:
            raise RuntimeError("Aliyun returned an empty translation")
        return translated

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        # Official Universal Edition input limit is 5,000 characters. BabelDOC
        # paragraphs are normally shorter; split only unusually large blocks.
        if len(text) <= self.max_chars:
            try:
                return self._one(text)
            except Exception as exc:
                gate.record_error(self.provider_id, str(exc))
                raise
        chunks: list[str] = []
        rest = text
        while len(rest) > self.max_chars:
            cut = max(rest.rfind("\n", 0, self.max_chars), rest.rfind(". ", 0, self.max_chars), rest.rfind("。", 0, self.max_chars), rest.rfind(" ", 0, self.max_chars))
            if cut < self.max_chars // 2:
                cut = self.max_chars
            chunks.append(rest[:cut + (1 if rest[cut:cut+1] in {"。"} else 0)])
            rest = rest[len(chunks[-1]):]
        if rest:
            chunks.append(rest)
        return "".join(self._one(chunk) for chunk in chunks)

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        raise NotImplementedError


@dataclass
class _MultiSlot:
    provider_id: str
    translator: BaseTranslator
    qps: float
    max_concurrency: int
    config: dict[str, Any]
    in_flight: int = 0
    failures: int = 0
    cooldown_until: float = 0.0


def _quota_failure_status(message: str) -> tuple[str | None, str]:
    low = str(message or "").lower()
    if "54004" in low or "please recharge" in low or "insufficient balance" in low or "quota exhausted" in low:
        return "exhausted", "provider_error"
    if "unsynchronized" in low or "service not activated" in low or "servicenotactivated" in low:
        return "unavailable", "provider_error"
    if "invalid sign" in low or "54001" in low or "signature" in low or "unauthorized" in low or "invalid credential" in low:
        return "unavailable", "provider_error"
    return None, "provider_error"


class MultiProviderTranslator(BaseTranslator):
    """BabelDOC translator that shards paragraphs over several providers."""
    name = "zft-multi"

    def __init__(self, lang_in: str, lang_out: str, slots: list[_MultiSlot], strategy: str = "balanced", ignore_cache: bool = False, quota_aware: bool = True):
        super().__init__(lang_in, lang_out, ignore_cache)
        if not slots:
            raise RuntimeError("multi-provider translator requires at least one provider")
        self.slots = slots
        self.strategy = strategy if strategy in {"balanced", "failover"} else "balanced"
        self.model = "multi:" + ",".join(x.provider_id for x in slots)
        self.lock = threading.RLock()
        self.quota_aware = bool(quota_aware)
        self.add_cache_impact_parameters("providers", self.model)
        self.add_cache_impact_parameters("quota_aware", self.quota_aware)
        self.add_cache_impact_parameters("strategy", self.strategy)

    def _ordered_slots(self, upcoming_chars: int = 0) -> list[_MultiSlot]:
        now = time.monotonic()
        with self.lock:
            available = [x for x in self.slots if x.cooldown_until <= now]
            if not available:
                available = list(self.slots)

            scored: list[tuple[_MultiSlot, float]] = []
            for slot in available:
                weight = 1.0
                if self.quota_aware:
                    eligible, snap = quota_manager.eligible(slot.provider_id, slot.config, upcoming_chars)
                    if not eligible:
                        continue
                    weight = max(0.01, float(snap.get("dispatch_weight") or 1.0))
                scored.append((slot, weight))

            if not scored:
                return []
            if self.strategy == "failover":
                return [x[0] for x in sorted(scored, key=lambda pair: self.slots.index(pair[0]))]
            # Weighted least-load: configured QPS is multiplied by quota health.
            # Low-balance engines remain usable, but naturally receive fewer paragraphs.
            return [x[0] for x in sorted(
                scored,
                key=lambda pair: (
                    1 if pair[0].in_flight >= pair[0].max_concurrency else 0,
                    (pair[0].in_flight + 1) / max(0.01, pair[0].qps * pair[1]),
                    pair[0].failures,
                    self.slots.index(pair[0]),
                ),
            )]

    def _call(self, slot: _MultiSlot, text: str, rate_limit_params: dict | None):
        with self.lock:
            slot.in_flight += 1
        try:
            result = slot.translator.do_translate(text, rate_limit_params)
            quota_manager.record_success(slot.provider_id, len(text), slot.config)
            with self.lock:
                slot.failures = max(0, slot.failures - 1)
            return result
        except Exception as exc:
            message = str(exc)
            status, source = _quota_failure_status(message)
            if status:
                quota_manager.mark(slot.provider_id, slot.config, status, message, source=source)
            with self.lock:
                slot.failures += 1
                # Hard provider/account failures stay out of this job longer; transient
                # failures get normal exponential cooldown. Persistent exclusion is
                # additionally enforced by QuotaManager.
                slot.cooldown_until = time.monotonic() + (300.0 if status in {"exhausted", "unavailable"} else min(60.0, 3.0 * (2 ** min(slot.failures, 4))))
            raise
        finally:
            with self.lock:
                slot.in_flight = max(0, slot.in_flight - 1)

    def do_translate(self, text, rate_limit_params: dict | None = None):
        if not text:
            return text
        remembered = translation_memory.get(text, self.lang_in, self.lang_out)
        if remembered is not None:
            return remembered
        errors: list[str] = []
        ordered = self._ordered_slots(len(text))
        if not ordered:
            snaps = [quota_manager.snapshot(x.provider_id, x.config) for x in self.slots]
            details = ", ".join(f"{x.provider_id}:{snap.get('status')}" for x, snap in zip(self.slots, snaps))
            raise RuntimeError("no translation provider is eligible after quota/health checks: " + details)
        for slot in ordered:
            try:
                result = self._call(slot, text, rate_limit_params)
                translation_memory.put(text, result, self.lang_in, self.lang_out, provider_id=slot.provider_id)
                return result
            except Exception as exc:
                errors.append(f"{slot.provider_id}: {exc}")
                continue
        raise RuntimeError("all translation providers failed: " + " | ".join(errors[-5:]))

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        # Keep BabelDOC on the generic translation path so classic MT engines and
        # LLM engines can safely coexist inside one pool.
        raise NotImplementedError



def provider_is_configured(row: ProviderProfile) -> bool:
    try:
        secrets = decrypt_json(row.secret_payload)
    except Exception:
        return False
    if row.kind == "baidu":
        mode = str((row.config or {}).get("auth_mode") or "sign").lower()
        return bool(secrets.get("app_id") and (secrets.get("api_key") if mode == "api_key" else secrets.get("secret_key")))
    if row.kind == "tencent":
        mode = str((row.config or {}).get("auth_mode") or "tmt_tc3").lower()
        return bool(secrets.get("api_key")) if mode == "tokenhub" else bool(secrets.get("secret_id") and secrets.get("secret_key"))
    if row.kind == "openai_compatible":
        return bool(secrets.get("api_key"))
    if row.kind == "volcengine":
        return bool(secrets.get("api_key"))
    if row.kind == "aliyun":
        return bool(secrets.get("access_key_id") and secrets.get("access_key_secret"))
    return False


def provider_record(provider_id: str) -> tuple[ProviderProfile, dict]:
    with SessionLocal() as db:
        row = db.get(ProviderProfile, provider_id)
        if row is None:
            raise RuntimeError(f"translation provider does not exist: {provider_id}")
        db.expunge(row)
    if not row.enabled:
        raise RuntimeError(f"translation provider is disabled: {provider_id}")
    return row, decrypt_json(row.secret_payload)


def create_translator(provider_id: str, lang_in: str, lang_out: str, fallback_qps: int = 1):
    row, secrets = provider_record(provider_id)
    qps, max_concurrency = _provider_limits(row, fallback_qps)
    config = row.config or {}
    if row.kind == "baidu":
        auth_mode = str(config.get("auth_mode") or "sign").strip().lower()
        app_id = str(secrets.get("app_id") or "").strip()
        secret_key = str(secrets.get("secret_key") or "").strip()
        api_key = str(secrets.get("api_key") or "").strip()
        if auth_mode == "api_key":
            endpoint = str(config.get("endpoint") or "https://fanyi-api.baidu.com/ait/api/aiTextTranslate").strip()
            if not app_id or not api_key:
                raise RuntimeError("百度大模型翻译已启用，但 APPID / API Key 未完整配置")
        else:
            endpoint = str(config.get("endpoint") or "https://fanyi-api.baidu.com/api/trans/vip/translate").strip()
            if not app_id or not secret_key:
                raise RuntimeError("百度通用翻译已启用，但 APPID / 开发者密钥未完整配置")
        return BaiduTranslator(lang_in, lang_out, row.id, qps, max_concurrency, app_id, endpoint,
                               auth_mode=auth_mode, secret_key=secret_key, api_key=api_key,
                               model_type=str(config.get("model_type") or "llm"), reference=str(config.get("reference") or ""))
    if row.kind == "openai_compatible":
        api_key = str(secrets.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("OpenAI-compatible provider is enabled but API key is empty")
        return CloudOpenAITranslator(lang_in, lang_out, row.id, qps, max_concurrency,
                                     str(config.get("model") or "gpt-4.1-mini"),
                                     str(config.get("base_url") or "https://api.openai.com/v1"), api_key)
    if row.kind == "tencent":
        auth_mode = str(config.get("auth_mode") or "tmt_tc3").strip().lower()
        if auth_mode in {"tmt_tc3", "legacy_tc3"}:
            secret_id = str(secrets.get("secret_id") or "").strip()
            secret_key = str(secrets.get("secret_key") or "").strip()
            if not secret_id or not secret_key:
                raise RuntimeError("腾讯机器翻译 TMT 需要 SecretId 和 SecretKey")
            return TencentTMTTranslator(
                lang_in, lang_out, row.id, qps, max_concurrency,
                str(config.get("tmt_endpoint") or "https://tmt.tencentcloudapi.com"),
                secret_id, secret_key,
                str(config.get("tmt_region") or "ap-beijing"),
                str(config.get("tmt_version") or "2018-03-21"),
                int(config.get("project_id") or 0),
                int(config.get("max_chars") or 1900),
            )
        if auth_mode == "hunyuan_tc3":
            secret_id = str(secrets.get("secret_id") or "").strip()
            secret_key = str(secrets.get("secret_key") or "").strip()
            if not secret_id or not secret_key:
                raise RuntimeError("腾讯混元 ChatTranslations 需要 SecretId 和 SecretKey")
            return TencentHunyuanTranslator(
                lang_in, lang_out, row.id, qps, max_concurrency,
                str(config.get("hunyuan_endpoint") or "https://hunyuan.ai.tencentcloudapi.com"),
                secret_id, secret_key,
                str(config.get("hunyuan_model") or "hunyuan-translation-lite"),
                str(config.get("field") or ""),
            )
        api_key = str(secrets.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("腾讯 TokenHub 翻译需要 API Key")
        return TencentTokenHubTranslator(
            lang_in, lang_out, row.id, qps, max_concurrency,
            str(config.get("model") or "hy-mt2-plus"),
            str(config.get("base_url") or "https://tokenhub.tencentmaas.com/v1"),
            api_key,
        )
    if row.kind == "volcengine":
        api_key = str(secrets.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("火山机器翻译需要 API Key")
        return VolcengineTranslator(
            lang_in, lang_out, row.id, qps, max_concurrency,
            str(config.get("endpoint") or "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate"),
            api_key, str(config.get("resource_id") or "volc.speech.mt"),
        )
    if row.kind == "aliyun":
        ak = str(secrets.get("access_key_id") or "").strip()
        sk = str(secrets.get("access_key_secret") or "").strip()
        if not ak or not sk:
            raise RuntimeError("阿里翻译需要 AccessKey ID 和 AccessKey Secret")
        return AliyunTranslator(lang_in, lang_out, row.id, qps, max_concurrency,
                                str(config.get("endpoint") or "https://mt.cn-hangzhou.aliyuncs.com"), ak, sk,
                                str(config.get("path") or "/api/translate/web/general"),
                                str(config.get("scene") or "general"), int(config.get("max_chars") or 4900))
    raise RuntimeError(f"Unsupported translator provider kind: {row.kind}")


def create_multi_translator(provider_ids: list[str], lang_in: str, lang_out: str, strategy: str = "balanced", quota_aware: bool = True) -> tuple[BaseTranslator, float, int]:
    clean: list[str] = []
    for value in provider_ids:
        value = str(value).strip()
        if value and value not in clean:
            clean.append(value)
    if not clean:
        raise RuntimeError("no translation provider selected")

    slots: list[_MultiSlot] = []
    total_qps = 0.0
    total_concurrency = 0
    for provider_id in clean:
        row, _ = provider_record(provider_id)
        qps, concurrency = _provider_limits(row, 1)
        translator = create_translator(provider_id, lang_in, lang_out, max(1, round(qps)))
        config = dict(row.config or {})
        slots.append(_MultiSlot(provider_id, translator, qps, concurrency, config))
        # Hard-exhausted/unavailable providers should not inflate BabelDOC's aggregate
        # launch rate for the next job. Unknown/low providers are weighted dynamically.
        if quota_aware:
            snap = quota_manager.snapshot(provider_id, config)
            if snap.get("status") in {"exhausted", "unavailable"}:
                continue
            weight = max(0.05, float(snap.get("dispatch_weight") or 1.0))
        else:
            weight = 1.0
        total_qps += qps * weight
        total_concurrency += concurrency
    if total_qps <= 0:
        # Keep one worker alive so BabelDOC can surface a precise quota error instead
        # of failing during config construction. MultiProviderTranslator will reject
        # the paragraph with provider-specific status details.
        total_qps = 1.0
        total_concurrency = 1
    return MultiProviderTranslator(lang_in, lang_out, slots, strategy=strategy, quota_aware=quota_aware), max(1.0, total_qps), max(1, total_concurrency)

def provider_limits(provider_id: str, fallback_qps: int = 1) -> tuple[float, int]:
    row, _ = provider_record(provider_id)
    return _provider_limits(row, fallback_qps)


def test_provider(provider_id: str) -> str:
    translator = create_translator(provider_id, "en", "zh-CN", 1)
    if isinstance(translator, CloudOpenAITranslator):
        result = translator.llm_translate("Translate to Simplified Chinese. Output translation only: Hello world")
    else:
        result = translator.translate("Hello world")
    if not result:
        raise RuntimeError("provider returned an empty test translation")
    return str(result)[:300]
