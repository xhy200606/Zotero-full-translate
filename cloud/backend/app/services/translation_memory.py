from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import TranslationMemoryEntry


PROFILE_KEY = "academic-v1"


def _now():
    return datetime.now(timezone.utc)


def normalize_source(text: str) -> str:
    """Normalize only formatting noise that should not change translation semantics.

    We intentionally do not lowercase or collapse all whitespace because BabelDOC
    placeholders, formulas and line breaks can be semantically significant.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def source_hash(text: str) -> str:
    return hashlib.sha256(normalize_source(text).encode("utf-8")).hexdigest()


class TranslationMemory:
    """Provider-independent translation memory persisted in the ZFT SQLite DB.

    The first successful translation for a language pair/profile becomes reusable
    by later jobs even when a different provider pool is selected. This is more
    stable than BabelDOC's provider-parameter cache and is intentionally stored in
    /data/zft.db so it survives container rebuilds.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def get(self, text: str, lang_in: str, lang_out: str, profile_key: str = PROFILE_KEY) -> str | None:
        normalized = normalize_source(text)
        if not normalized:
            return None
        key = source_hash(normalized)
        with SessionLocal() as db:
            row = db.scalar(select(TranslationMemoryEntry).where(
                TranslationMemoryEntry.source_hash == key,
                TranslationMemoryEntry.lang_in == str(lang_in),
                TranslationMemoryEntry.lang_out == str(lang_out),
                TranslationMemoryEntry.profile_key == str(profile_key),
            ))
            if row is None:
                with self.lock:
                    self.misses += 1
                return None
            row.hit_count = int(row.hit_count or 0) + 1
            row.last_used_at = _now()
            db.commit()
            value = row.translated_text
        with self.lock:
            self.hits += 1
        return value

    def put(self, text: str, translated: str, lang_in: str, lang_out: str,
            provider_id: str | None = None, profile_key: str = PROFILE_KEY) -> None:
        normalized = normalize_source(text)
        translated = str(translated or "").strip()
        if not normalized or not translated:
            return
        key = source_hash(normalized)
        with SessionLocal() as db:
            row = db.scalar(select(TranslationMemoryEntry).where(
                TranslationMemoryEntry.source_hash == key,
                TranslationMemoryEntry.lang_in == str(lang_in),
                TranslationMemoryEntry.lang_out == str(lang_out),
                TranslationMemoryEntry.profile_key == str(profile_key),
            ))
            if row is None:
                row = TranslationMemoryEntry(
                    source_hash=key,
                    lang_in=str(lang_in),
                    lang_out=str(lang_out),
                    profile_key=str(profile_key),
                    source_text=normalized,
                    translated_text=translated,
                    provider_id=provider_id,
                    hit_count=0,
                )
                db.add(row)
            else:
                # Preserve the first successful translation by default. This avoids
                # different providers constantly replacing a stable cached wording.
                if not row.translated_text:
                    row.translated_text = translated
                if not row.provider_id:
                    row.provider_id = provider_id
                row.last_used_at = _now()
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return
        with self.lock:
            self.writes += 1

    def stats(self) -> dict[str, Any]:
        with SessionLocal() as db:
            total = int(db.scalar(select(func.count()).select_from(TranslationMemoryEntry)) or 0)
            chars = int(db.scalar(select(func.coalesce(func.sum(func.length(TranslationMemoryEntry.source_text)), 0))) or 0)
            hits_db = int(db.scalar(select(func.coalesce(func.sum(TranslationMemoryEntry.hit_count), 0))) or 0)
        with self.lock:
            process = {"process_hits": self.hits, "process_misses": self.misses, "process_writes": self.writes}
        return {"entries": total, "stored_source_chars": chars, "reuse_hits": hits_db, **process}

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with SessionLocal() as db:
            rows = db.scalars(select(TranslationMemoryEntry).order_by(desc(TranslationMemoryEntry.last_used_at)).limit(limit)).all()
            return [{
                "id": x.id,
                "lang_in": x.lang_in,
                "lang_out": x.lang_out,
                "profile_key": x.profile_key,
                "source_text": x.source_text,
                "translated_text": x.translated_text,
                "provider_id": x.provider_id,
                "hit_count": x.hit_count,
                "created_at": x.created_at.isoformat() if x.created_at else None,
                "last_used_at": x.last_used_at.isoformat() if x.last_used_at else None,
            } for x in rows]


translation_memory = TranslationMemory()
