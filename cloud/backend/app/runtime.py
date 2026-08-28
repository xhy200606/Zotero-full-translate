from __future__ import annotations

from dataclasses import dataclass
from .db import SessionLocal
from .models import RuntimeConfig


@dataclass(frozen=True)
class RuntimeSnapshot:
    default_provider: str
    default_provider_ids: list[str]
    default_provider_strategy: str
    max_active_jobs: int
    babeldoc_qps: int
    pool_max_workers: int
    multi_pool_max_workers: int
    aggregate_qps_cap: int
    quota_aware_dispatch: bool
    report_interval: float
    max_pages_per_part: int
    skip_scanned_detection: bool
    auto_ocr_workaround: bool


def get_runtime_snapshot() -> RuntimeSnapshot:
    with SessionLocal() as db:
        row = db.get(RuntimeConfig, 1)
        if row is None:
            raise RuntimeError("runtime configuration has not been initialized")
        return RuntimeSnapshot(
            default_provider=row.default_provider,
            default_provider_ids=list(getattr(row, "default_provider_ids", None) or [row.default_provider]),
            default_provider_strategy=str(getattr(row, "default_provider_strategy", None) or "balanced"),
            max_active_jobs=max(1, int(row.max_active_jobs)),
            babeldoc_qps=max(1, int(row.babeldoc_qps)),
            pool_max_workers=max(1, int(row.pool_max_workers)),
            multi_pool_max_workers=max(1, int(getattr(row, "multi_pool_max_workers", 12) or 12)),
            aggregate_qps_cap=max(1, int(getattr(row, "aggregate_qps_cap", 100) or 100)),
            quota_aware_dispatch=bool(getattr(row, "quota_aware_dispatch", True)),
            report_interval=max(0.1, float(row.report_interval)),
            max_pages_per_part=max(1, int(row.max_pages_per_part)),
            skip_scanned_detection=bool(row.skip_scanned_detection),
            auto_ocr_workaround=bool(row.auto_ocr_workaround),
        )
