"""Static contract checks for Zotero 0.3.7 local-sync/retranslation behavior."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
main = (ROOT / "zotero-plugin/chrome/content/main.js").read_text(encoding="utf-8")

required = [
    "findSyncedLocalTranslationPath",
    "localPDFMatchesSHA256",
    "cachedFileSHA256",
    "documentDOIForAttachment",
    "cloudSetDocumentState",
    "mono_sha256",
    "跳过下载",
    "collectManagedTranslationAttachments",
    "removeSupersededTranslationAttachments",
    "previousTranslationIDs",
]
for token in required:
    assert token in main, token

                                                                      
assert main.index("previousTranslationIDs") < main.index("removeSupersededTranslationAttachments")
assert "if (forceRetranslate && attachment?.id && previousTranslationIDs.length)" in main

print("zotero-local-sync-smoke: ok")
