from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/app/services/providers.py"
text = path.read_text(encoding="utf-8")
tree = ast.parse(text)
cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "VolcengineTranslator")
method = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == "do_translate")
src = ast.get_source_segment(text, method) or ""
for token in [
    '"source_language": source',
    '"target_language": target',
    '"text_list": [text]',
    '"Content-Type": "application/json"',
    '"x-api-key": self.api_key',
    '"X-Api-Resource-Id": self.resource_id',
    '"X-Api-Request-Id": request_id',
    'self.client.post(self.endpoint, json=payload, headers=headers)',
]:
    assert token in src, token
assert 'str(uuid.uuid4())' in src
assert 'X-Api-Status-Code' in (ROOT / 'backend/app/services/providers.py').read_text(encoding='utf-8')
assert '20000000' in (ROOT / 'backend/app/services/providers.py').read_text(encoding='utf-8')
print('volc-speech-api-smoke: ok')
