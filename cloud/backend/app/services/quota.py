from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..db import SessionLocal
from ..models import ProviderQuotaState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month_key(now: datetime | None = None) -> str:
    now = now or _now()
    return f"{now.year:04d}-{now.month:02d}"


def _next_month(now: datetime | None = None) -> datetime:
    now = now or _now()
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)


def _int_or_none(value: Any) -> int | None:
    try:
        value = int(float(value))
    except Exception:
        return None
    return value if value > 0 else None


def quota_config(config: dict | None) -> dict[str, Any]:
    cfg = config or {}
    total = _int_or_none(cfg.get("quota_total_chars"))
    reserve = max(0, int(float(cfg.get("quota_reserve_chars") or 0)))
    low = max(1.0, min(99.0, float(cfg.get("quota_low_percent") or 10.0)))
    enabled = bool(cfg.get("quota_enabled", True))
    period = str(cfg.get("quota_period") or "month").strip().lower()
    if period not in {"month", "account"}:
        period = "month"
    return {
        "enabled": enabled,
        "total_chars": total,
        "reserve_chars": reserve,
        "low_percent": low,
        "period": period,
    }


@dataclass
class _MemState:
    period_key: str
    local_used_chars: int = 0
    remote_used_chars: int | None = None
    remote_sync_local_chars: int = 0
    status: str = "unknown"
    source: str = "local_meter"
    last_sync_at: datetime | None = None
    reset_at: datetime | None = None
    last_error: str | None = None
    dirty_chars: int = 0
    last_flush_mono: float = 0.0


class QuotaManager:
    """Persistent quota telemetry plus a low-overhead in-memory meter.

    Cloud vendors are inconsistent: some expose usage but not balance, some expose
    only per-request metering, and some expose neither. ZFT therefore separates
    *meter source* from *configured budget* and labels estimated balances clearly.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self._states: dict[str, _MemState] = {}

    def _period_key(self, config: dict | None) -> tuple[str, datetime | None]:
        qc = quota_config(config)
        if qc["period"] == "month":
            return _month_key(), _next_month()
        return "account", None

    def _load(self, provider_id: str, config: dict | None) -> _MemState:
        period_key, reset_at = self._period_key(config)
        with self.lock:
            state = self._states.get(provider_id)
            if state is not None:
                if state.period_key != period_key:
                    state = _MemState(period_key=period_key, reset_at=reset_at, last_flush_mono=time.monotonic())
                    self._states[provider_id] = state
                    self._persist(provider_id, state)
                return state
        with SessionLocal() as db:
            row = db.get(ProviderQuotaState, provider_id)
            if row is None or row.period_key != period_key:
                state = _MemState(period_key=period_key, reset_at=reset_at, last_flush_mono=time.monotonic())
            else:
                state = _MemState(
                    period_key=row.period_key,
                    local_used_chars=max(0, int(row.local_used_chars or 0)),
                    remote_used_chars=None if row.remote_used_chars is None else max(0, int(row.remote_used_chars)),
                    remote_sync_local_chars=max(0, int(row.remote_sync_local_chars or 0)),
                    status=str(row.status or "unknown"),
                    source=str(row.source or "local_meter"),
                    last_sync_at=row.last_sync_at,
                    reset_at=row.reset_at or reset_at,
                    last_error=row.last_error,
                    last_flush_mono=time.monotonic(),
                )
        with self.lock:
            self._states[provider_id] = state
        return state

    def _persist(self, provider_id: str, state: _MemState) -> None:
        with SessionLocal() as db:
            row = db.get(ProviderQuotaState, provider_id)
            if row is None:
                row = ProviderQuotaState(provider_id=provider_id)
                db.add(row)
            row.period_key = state.period_key
            row.local_used_chars = int(state.local_used_chars)
            row.remote_used_chars = state.remote_used_chars
            row.remote_sync_local_chars = int(state.remote_sync_local_chars)
            row.status = state.status
            row.source = state.source
            row.last_sync_at = state.last_sync_at
            row.reset_at = state.reset_at
            row.last_error = state.last_error
            row.updated_at = _now()
            db.commit()
        state.dirty_chars = 0
        state.last_flush_mono = time.monotonic()

    def _flush_if_needed(self, provider_id: str, state: _MemState, force: bool = False) -> None:
        if force or state.dirty_chars >= 5000 or (state.dirty_chars > 0 and time.monotonic() - state.last_flush_mono >= 5.0):
            self._persist(provider_id, state)

    def flush_all(self) -> None:
        with self.lock:
            items = list(self._states.items())
        for provider_id, state in items:
            try:
                self._flush_if_needed(provider_id, state, force=True)
            except Exception:
                pass

    def _effective_used(self, state: _MemState) -> int:
        if state.remote_used_chars is None:
            return max(0, int(state.local_used_chars))
        since_sync = max(0, int(state.local_used_chars) - int(state.remote_sync_local_chars))
        return max(0, int(state.remote_used_chars) + since_sync)

    def _derive(self, state: _MemState, config: dict | None) -> dict[str, Any]:
        qc = quota_config(config)
        used = self._effective_used(state)
        total = qc["total_chars"]
        remaining = max(0, total - used) if total is not None else None
        pct = round((remaining / total) * 100.0, 2) if total and remaining is not None else None
        hard = state.status in {"exhausted", "unavailable"}
        status = state.status
        if not qc["enabled"]:
            status = "disabled"
        elif not hard:
            if remaining is not None and remaining <= qc["reserve_chars"]:
                status = "exhausted"
            elif pct is not None and pct <= qc["low_percent"]:
                status = "low"
            elif total is not None:
                status = "ok"
            elif state.last_error:
                status = "warning"
            elif used > 0:
                status = "metered"
            else:
                status = "unknown"

        if not qc["enabled"]:
            weight = 1.0
        elif status in {"exhausted", "unavailable"}:
            weight = 0.0
        elif pct is None:
            weight = 1.0
        elif pct <= qc["low_percent"]:
            weight = max(0.08, pct / max(1.0, qc["low_percent"]) * 0.30)
        elif pct <= 25:
            weight = 0.50
        else:
            weight = min(1.0, 0.55 + pct / 220.0)

        source = state.source
        if state.remote_used_chars is not None:
            source = "provider_api+local_delta"
        elif total is not None and source == "local_meter":
            source = "manual_budget+local_meter"

        return {
            "enabled": qc["enabled"],
            "period": qc["period"],
            "period_key": state.period_key,
            "status": status,
            "source": source,
            "total_chars": total,
            "used_chars": used,
            "remaining_chars": remaining,
            "remaining_percent": pct,
            "reserve_chars": qc["reserve_chars"],
            "low_percent": qc["low_percent"],
            "dispatch_weight": round(weight, 4),
            "reset_at": state.reset_at.isoformat() if state.reset_at else None,
            "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
            "last_error": state.last_error,
        }

    def snapshot(self, provider_id: str, config: dict | None) -> dict[str, Any]:
        state = self._load(provider_id, config)
        with self.lock:
            return self._derive(state, config)

    def eligible(self, provider_id: str, config: dict | None, upcoming_chars: int = 0) -> tuple[bool, dict[str, Any]]:
        snap = self.snapshot(provider_id, config)
        if not snap["enabled"]:
            return True, snap
        if snap["status"] in {"exhausted", "unavailable"}:
            return False, snap
        remaining = snap.get("remaining_chars")
        if remaining is not None and remaining <= int(snap.get("reserve_chars") or 0) + max(0, int(upcoming_chars)):
            snap = dict(snap)
            snap["status"] = "exhausted"
            snap["dispatch_weight"] = 0.0
            return False, snap
        return True, snap

    def record_success(self, provider_id: str, chars: int, config: dict | None, *, source: str = "local_meter") -> None:
        state = self._load(provider_id, config)
        chars = max(0, int(chars or 0))
        with self.lock:
            state.local_used_chars += chars
            state.dirty_chars += chars
            # A real successful translation is the strongest recovery signal.
            if state.status in {"unavailable", "exhausted", "warning"}:
                state.status = "ok" if quota_config(config)["total_chars"] else "metered"
                state.last_error = None
            if source and state.remote_used_chars is None:
                state.source = source
            self._flush_if_needed(provider_id, state)

    def set_remote_usage(self, provider_id: str, used_chars: int, config: dict | None, *, source: str = "provider_api") -> dict[str, Any]:
        state = self._load(provider_id, config)
        with self.lock:
            state.remote_used_chars = max(0, int(used_chars or 0))
            state.remote_sync_local_chars = int(state.local_used_chars)
            state.last_sync_at = _now()
            state.source = source
            if state.status not in {"exhausted", "unavailable"}:
                state.status = "ok" if quota_config(config)["total_chars"] else "metered"
            state.last_error = None
            self._flush_if_needed(provider_id, state, force=True)
            return self._derive(state, config)

    def mark(self, provider_id: str, config: dict | None, status: str, message: str, *, source: str = "provider_error") -> dict[str, Any]:
        state = self._load(provider_id, config)
        with self.lock:
            state.status = status
            state.source = source
            state.last_error = str(message)[:800]
            state.last_sync_at = _now()
            self._flush_if_needed(provider_id, state, force=True)
            return self._derive(state, config)

    def clear_status(self, provider_id: str, config: dict | None) -> dict[str, Any]:
        state = self._load(provider_id, config)
        with self.lock:
            state.status = "unknown"
            state.last_error = None
            state.last_sync_at = _now()
            self._flush_if_needed(provider_id, state, force=True)
            return self._derive(state, config)

    def reset_local_usage(self, provider_id: str, config: dict | None) -> dict[str, Any]:
        state = self._load(provider_id, config)
        with self.lock:
            state.local_used_chars = 0
            state.remote_used_chars = None
            state.remote_sync_local_chars = 0
            state.status = "unknown"
            state.source = "local_meter"
            state.last_error = None
            state.last_sync_at = _now()
            state.dirty_chars = 0
            self._flush_if_needed(provider_id, state, force=True)
            return self._derive(state, config)


quota_manager = QuotaManager()
