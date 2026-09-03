#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
VERSION="$(python3 - <<'PY'
import json
print(json.load(open('manifest.json', encoding='utf-8'))['version'])
PY
)"
mkdir -p dist
OUT="dist/Zotero-full-translate-v${VERSION}.xpi"
rm -f "$OUT"
zip -X -q -r "$OUT" \
  bootstrap.js chrome.manifest manifest.json prefs.js LICENSE \
  chrome
echo "$OUT"
