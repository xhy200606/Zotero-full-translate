import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-security-defaults-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root/'zft.db'}"
os.environ["ZFT_STORAGE_DIR"] = str(root / "files")
os.environ["ZFT_WORK_DIR"] = str(root / "work")
os.environ.pop("ZFT_API_KEY", None)
os.environ.pop("ZFT_CONFIG_SECRET", None)

from app.config import get_settings
from app.auth import _valid_service_key
from app.crypto import encrypt_json

assert get_settings().zft_api_key == ""
assert not _valid_service_key("change-me")
try:
    encrypt_json({"api_key": "do-not-store-insecurely"})
except RuntimeError as exc:
    assert "ZFT_CONFIG_SECRET" in str(exc)
else:
    raise AssertionError("provider secret encryption must fail closed without a configured secret")

print("security-defaults-smoke: ok")
