import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.config import configs

claim_lock = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id       TEXT,
    name            TEXT NOT NULL,
    payload         TEXT,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'CLAIMED', 'RUNNING', 'COMPLETED', 'FAILED')),
    claim_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 5,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    retryable       INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_retry_at   DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status_retryable ON jobs(status, retryable, retry_count, max_retries, next_retry_at);
"""


class Database:
    def __init__(self, db_path: str | None = None):
        self._path = Path(db_path or configs.configs.DB_PATH)

    @property
    def path(self) -> Path:
        return self._path

    def set_path(self, path: Path) -> None:
        self._path = path

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=configs.configs.DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.session() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "UPDATE jobs SET status = 'FAILED', retryable = 1 "
                "WHERE status = 'retry_wait'"
            )

    @contextmanager
    def session(self):
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
