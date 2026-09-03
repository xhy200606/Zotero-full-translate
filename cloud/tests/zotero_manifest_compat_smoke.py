from pathlib import Path
import json
from urllib.parse import urlparse

root=Path(__file__).resolve().parents[2]
m=json.loads((root/'zotero-plugin/manifest.json').read_text())
z=m['applications']['zotero']
assert m['version']=='0.4.1'
assert z['strict_min_version']=='9.0'
assert z['strict_max_version']=='10.0.*'
assert z['id']=='zotero-fulltext-translator@zft.local'
                                                                        
                                                                         
update_url=z.get('update_url','')
assert update_url and urlparse(update_url).scheme == 'https'
assert m.get('homepage_url','').startswith('https://')
print('zotero-manifest-compat-smoke: ok')
