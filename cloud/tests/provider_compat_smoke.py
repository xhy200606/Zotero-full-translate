from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
providers = (ROOT / "backend/app/services/providers.py").read_text(encoding="utf-8")
bootstrap = (ROOT / "backend/app/bootstrap.py").read_text(encoding="utf-8")
api = (ROOT / "backend/app/api/providers.py").read_text(encoding="utf-8")
ui = (ROOT / "frontend/src/App.jsx").read_text(encoding="utf-8")
env = (ROOT / ".env.example").read_text(encoding="utf-8")

for text in (providers, bootstrap, api):
    ast.parse(text)

# Tencent compatibility remains available.
for token in [
    "class TencentTMTTranslator",
    '"X-TC-Action": "TextTranslate"',
    '"X-TC-Version": self.version',
    'https://tmt.tencentcloudapi.com',
    'class TencentHunyuanTranslator',
]:
    assert token in providers, token

# v1.4.1 Volcengine uses the API-key Machine Translation endpoint supplied by the product docs.
for token in [
    "class VolcengineTranslator",
    "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate",
    '"x-api-key": self.api_key',
    '"X-Api-Resource-Id": self.resource_id',
    '"X-Api-Request-Id": request_id',
    '"source_language": source',
    '"target_language": target',
    '"text_list": [text]',
    'resource_id: str = "volc.speech.mt"',
]:
    assert token in providers, token

for stale in [
    "https://open.volcengineapi.com",
    "https://translate.volcengineapi.com",
    '"auto", "current_2025", "legacy_2020"',
]:
    assert stale not in providers and stale not in api and stale not in ui, stale

assert '"volcengine": {"api_key"}' in api
assert '"volcengine": {"endpoint", "resource_id", "qps"' in api
assert 'VOLC_API_KEY=' in env
assert 'VOLC_ACCESS_KEY_ID=' not in env and 'VOLC_SECRET_ACCESS_KEY=' not in env
assert 'cfg["endpoint"] = "https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate"' in bootstrap
assert 'cfg["resource_id"] = "volc.speech.mt"' in bootstrap
for token in ['火山机器翻译', 'Resource ID', 'API Key']:
    assert token in ui, token
print("provider-compat-smoke: ok")
