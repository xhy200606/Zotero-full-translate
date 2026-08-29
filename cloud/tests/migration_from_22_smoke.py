"""Exercise the DOI migration against the important Cloud 2.2 binding shape."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-migrate22-"))
db_path = root / "legacy.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ZFT_CONFIG_SECRET"] = "migration-test-secret-material-0123456789"

                                                                               
                                                                                
conn = sqlite3.connect(db_path)
conn.executescript(
    """
    CREATE TABLE user_document_bindings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id VARCHAR(40) NOT NULL,
      source_sha256 VARCHAR(64) NOT NULL,
      lang_in VARCHAR(32) NOT NULL DEFAULT 'en',
      lang_out VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
      pages_key VARCHAR(128) NOT NULL DEFAULT '',
      output_mode VARCHAR(16) NOT NULL DEFAULT 'mono',
      bound_job_id VARCHAR(40) NOT NULL,
      created_at DATETIME,
      updated_at DATETIME,
      CONSTRAINT uq_user_document_binding UNIQUE
        (user_id, source_sha256, lang_in, lang_out, pages_key, output_mode)
    );
    INSERT INTO user_document_bindings
      (user_id, source_sha256, lang_in, lang_out, pages_key, output_mode, bound_job_id)
    VALUES
      ('legacy-user', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
       'en', 'zh-CN', '', 'mono', 'legacy-job');
    """
)
conn.commit(); conn.close()

from app.db import Base, engine, run_alembic_upgrade              
from app import models                   

                                                                        
Base.metadata.create_all(bind=engine)
run_alembic_upgrade()

conn = sqlite3.connect(db_path)
cols = {row[1]: row for row in conn.execute("PRAGMA table_info(user_document_bindings)")}
assert cols["source_sha256"][3] == 0, cols["source_sha256"]                
assert cols["bound_job_id"][3] == 0, cols["bound_job_id"]
assert "document_doi" in cols and "current_version_id" in cols
assert conn.execute("SELECT COUNT(*) FROM user_document_bindings WHERE user_id='legacy-user'").fetchone()[0] == 1

                                                                                         
conn.execute(
    """INSERT INTO user_document_bindings
       (user_id, document_doi, source_sha256, lang_in, lang_out, pages_key,
        output_mode, current_version_id, bound_job_id)
       VALUES (?, ?, NULL, 'en', 'zh-CN', '', 'mono', ?, NULL)""",
    ("new-user", "10.1000/test", "version-1"),
)
conn.commit()
assert conn.execute("SELECT COUNT(*) FROM user_document_bindings WHERE document_doi='10.1000/test'").fetchone()[0] == 1
assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_cloud_23"
conn.close()
print("migration-from-22-smoke: ok")
