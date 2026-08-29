from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if _is_sqlite:
    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record):                                 
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
                                                                                    
                                                                      
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_v11_columns() -> None:
    """Legacy compatibility for pre-migration ZFT databases.

    Cloud 2.3 introduces Alembic. These helpers remain temporarily so old 1.x/2.0
    databases can be lifted to the 2.2 baseline before formal revisions run.
    """
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing = {x["name"] for x in inspector.get_columns("jobs")}
    additions = {
        "client_id": "VARCHAR(128)",
        "client_request_id": "VARCHAR(160)",
        "client_item_key": "VARCHAR(160)",
        "worker_name": "VARCHAR(255)",
        "provider_ids": "JSON",
        "provider_strategy": "VARCHAR(24) DEFAULT 'single'",
        "source_sha256": "VARCHAR(64)",
        "reused_from_job_id": "VARCHAR(40)",
        "reuse_count": "INTEGER DEFAULT 0",
        "user_id": "VARCHAR(40)",
        "device_id": "VARCHAR(40)",
        "api_key_id": "VARCHAR(40)",
        "source_bytes": "INTEGER DEFAULT 0",
        "result_bytes": "INTEGER DEFAULT 0",
        "cache_hit": "BOOLEAN DEFAULT 0",
        "mono_sha256": "VARCHAR(64)",
        "dual_sha256": "VARCHAR(64)",
    }
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_client_id ON jobs (client_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_client_request_id ON jobs (client_request_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_client_item_key ON jobs (client_item_key)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_client_request_idx ON jobs (client_id, client_request_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_source_sha256 ON jobs (source_sha256)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_reused_from_job_id ON jobs (reused_from_job_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_user_id ON jobs (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_device_id ON jobs (device_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_api_key_id ON jobs (api_key_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_cache_hit ON jobs (cache_hit)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_mono_sha256 ON jobs (mono_sha256)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_dual_sha256 ON jobs (dual_sha256)"))

    inspector = inspect(engine)
    if "runtime_config" in inspector.get_table_names():
        runtime_existing = {x["name"] for x in inspector.get_columns("runtime_config")}
        with engine.begin() as conn:
            if "default_provider_ids" not in runtime_existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN default_provider_ids JSON"))
            if "default_provider_strategy" not in runtime_existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN default_provider_strategy VARCHAR(24) DEFAULT 'balanced'"))
            if "multi_pool_max_workers" not in runtime_existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN multi_pool_max_workers INTEGER DEFAULT 12"))
            if "aggregate_qps_cap" not in runtime_existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN aggregate_qps_cap INTEGER DEFAULT 100"))
            if "quota_aware_dispatch" not in runtime_existing:
                conn.execute(text("ALTER TABLE runtime_config ADD COLUMN quota_aware_dispatch BOOLEAN DEFAULT 1"))


def ensure_v22_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "devices" in tables:
            existing = {x["name"] for x in inspect(engine).get_columns("devices")}
            if "last_api_key_id" not in existing:
                conn.execute(text("ALTER TABLE devices ADD COLUMN last_api_key_id VARCHAR(40)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_last_api_key_id ON devices (last_api_key_id)"))
        if "usage_events" in tables:
            existing = {x["name"] for x in inspect(engine).get_columns("usage_events")}
            if "api_key_id" not in existing:
                conn.execute(text("ALTER TABLE usage_events ADD COLUMN api_key_id VARCHAR(40)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_events_api_key_id ON usage_events (api_key_id)"))


def run_alembic_upgrade() -> None:
    """Apply formal schema revisions after the legacy 2.2 baseline is available."""
    try:
        from alembic import command
        from alembic.config import Config
    except Exception as exc:                                                          
        raise RuntimeError("Alembic is required for Cloud schema upgrades") from exc
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
