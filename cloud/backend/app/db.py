from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_v11_columns() -> None:
    """Best-effort schema compatibility for older ZFT tables, including SQLite."""
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

    # v1.3 multi-engine runtime columns
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
