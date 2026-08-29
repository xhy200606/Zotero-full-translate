from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'backend/app').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))

jobs = (ROOT / 'backend/app/api/jobs.py').read_text(encoding='utf-8')
tasks = (ROOT / 'backend/app/task_manager.py').read_text(encoding='utf-8')
adapter = (ROOT / 'backend/app/services/babeldoc_adapter.py').read_text(encoding='utf-8')
providers = (ROOT / 'backend/app/services/providers.py').read_text(encoding='utf-8')
tm = (ROOT / 'backend/app/services/translation_memory.py').read_text(encoding='utf-8')
plugin = (ROOT.parent / 'zotero-plugin/chrome/content/main.js').read_text(encoding='utf-8')

for token in [
    'force_retranslate: Annotated[bool, Form()] = False',
    'reusable = None if force_retranslate or not doi else _find_reusable_job',
    '"ignore_cache": bool(force_retranslate)',
]:
    assert token in jobs, token
for token in ['ignore_cache=ignore_cache', 'cache_bypass']:
    assert token in tasks, token
assert 'ignore_cache: bool = False' in adapter
assert 'ignore_cache=ignore_cache' in adapter
assert 'None if self.zft_ignore_cache else translation_memory.get' in providers
assert 'replace=self.zft_ignore_cache' in providers
assert 'replace: bool = False' in tm
assert 'if replace or not row.translated_text' in tm
assert 'form.append("force_retranslate", "true")' in plugin
assert 'cloud.compareOpen' in plugin
assert 'scheduleNativeCompareRestore' in plugin
assert 'cloudHandleAuthFailure' in plugin
assert 'Cloud API Key 无效或已被撤销' in plugin
assert 'bind_completed_job' in tasks

print('force-retranslate-and-restart-sync-smoke: ok')
