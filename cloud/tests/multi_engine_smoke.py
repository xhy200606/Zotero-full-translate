from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
providers = (ROOT / 'backend/app/services/providers.py').read_text(encoding='utf-8')
bootstrap = (ROOT / 'backend/app/bootstrap.py').read_text(encoding='utf-8')
jobs = (ROOT / 'backend/app/api/jobs.py').read_text(encoding='utf-8')
system = (ROOT / 'backend/app/api/system.py').read_text(encoding='utf-8')
models = (ROOT / 'backend/app/models.py').read_text(encoding='utf-8')
ui = (ROOT / 'user-frontend/src/App.jsx').read_text(encoding='utf-8')
css = (ROOT / 'user-frontend/src/style.css').read_text(encoding='utf-8')

for path in (ROOT / 'backend/app').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))

for needle in [
    'class MultiProviderTranslator',
    'class TencentTokenHubTranslator',
    'class TencentTMTTranslator',
    'class TencentHunyuanTranslator',
    'class VolcengineTranslator',
    'class AliyunTranslator',
    'ChatTranslations',
    'https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate',
    'hy-mt2-plus',
    '/api/translate/web/general',
    'max_concurrency',
    'cooldown_until',
]:
    assert needle in providers, needle

for provider_id in ['"baidu"', '"tencent"', '"volcengine"', '"aliyun"', '"openai_compatible"']:
    assert provider_id in bootstrap, provider_id

for needle in ['providers:', 'provider_strategy:', 'default_provider_ids', 'default_provider_strategy']:
    assert needle in jobs or needle in system or needle in models, needle

assert 'provider_ids: Mapped[list]' in models
assert 'default_provider_ids: Mapped[list]' in models
assert 'aggregate_qps_cap' in models
assert 'multi_pool_max_workers' in models

for needle in ['均衡分流','故障转移','腾讯机器翻译','火山机器翻译','阿里机器翻译','默认翻译池','API Base URL']:
    assert needle in ui, needle

assert '.provider-user-card' in css
assert '.default-pool' in css
print('multi-engine-smoke: ok')
