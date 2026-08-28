from __future__ import annotations

import os
import shutil
from pathlib import Path
from .config import get_settings

settings = get_settings()


def ensure_bucket(retries: int = 1):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.work_dir.mkdir(parents=True, exist_ok=True)


def path_for(key: str) -> Path:
    key = key.replace("\\", "/").lstrip("/")
    if ".." in Path(key).parts:
        raise ValueError("invalid storage key")
    root = settings.storage_dir.resolve()
    path = (root / key).resolve()
    if root not in path.parents and path != root:
        raise ValueError("storage key escapes data directory")
    return path


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream"):
    ensure_bucket()
    path = path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def download_to(key: str, path: str | Path):
    src = path_for(key)
    if not src.is_file():
        raise FileNotFoundError(key)
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def upload_file(key: str, path: str | Path, content_type: str = "application/pdf"):
    ensure_bucket()
    src = Path(path)
    dst = path_for(key)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def exists(key: str) -> bool:
    return path_for(key).is_file()
