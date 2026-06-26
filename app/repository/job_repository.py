import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.model.job_models import JobStatus, JobModel
from app.repository.database import Database


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def _serialize_payload(self, payload: dict | None) -> str | None:
        if payload is None:
            return None
        return json.dumps(payload) if isinstance(payload, dict) else payload

    def create(self, model: JobModel) -> JobModel:
        with self.db.session() as conn:
            cursor = conn.execute(
                """INSERT INTO jobs (name, status, payload,
                   retry_count, max_retries, last_error, retryable)
                   VALUES (?, 'PENDING', ?, ?, ?, ?, ?)""",
                (
                    model.name,
                    self._serialize_payload(model.payload),
                    model.retry_count,
                    model.max_retries,
                    model.last_error,
                    int(model.retryable),
                ),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._to_model(row)

    def get(self, job_id: int) -> JobModel | None:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def list_all(
        self,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobModel]:
        with self.db.session() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (status.value, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_all(self, status: JobStatus | None = None) -> int:
        with self.db.session() as conn:
            if status:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = ?",
                    (status.value,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return row[0] if row else 0

    def claim_job(self, job_id: int, worker_id: str) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'CLAIMED',
                    worker_id = ?,
                    claim_count = claim_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING'
                """,
                (worker_id, job_id),
            )
            if cursor.rowcount != 1:
                return None
            updated = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._to_model(updated)

    def claim_next_pending(self, worker_id: str) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'PENDING'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            job_id = row["id"]
            conn.execute(
                """
                UPDATE jobs
                SET status = 'CLAIMED',
                    worker_id = ?,
                    claim_count = claim_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'PENDING'
                """,
                (worker_id, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._to_model(updated)

    def claim_next_retryable(self, worker_id: str) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'FAILED'
                  AND retryable = 1
                  AND retry_count < max_retries
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not row:
                return None
            job_id = row["id"]
            conn.execute(
                """
                UPDATE jobs
                SET status = 'CLAIMED',
                    worker_id = ?,
                    claim_count = claim_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'FAILED'
                """,
                (worker_id, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._to_model(updated)

    def recover_stuck_jobs(self, timeout_minutes: int = 5) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'FAILED',
                    last_error = 'Worker timeout — job recovered as failed',
                    retryable = 1,
                    worker_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('CLAIMED', 'RUNNING')
                  AND updated_at < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def update_status(
        self,
        job_id: int,
        status: JobStatus,
        *,
        expected_status: JobStatus | None = None,
        expected_worker_id: str | None = None,
    ) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            set_clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[object] = [status.value]
            if status == JobStatus.COMPLETED:
                set_clauses.extend(
                    [
                        "retryable = 0",
                        "next_retry_at = NULL",
                        "last_error = NULL",
                    ]
                )
            params.append(job_id)
            where = ["id = ?"]
            if expected_status is not None:
                where.append("status = ?")
                params.append(expected_status.value)
            if expected_worker_id is not None:
                where.append("worker_id = ?")
                params.append(expected_worker_id)
            cursor = conn.execute(
                f"UPDATE jobs SET {', '.join(set_clauses)} WHERE {' AND '.join(where)}",
                tuple(params),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def fail_job(
        self,
        job_id: int,
        retryable: bool,
        error_message: str | None,
        retry_count: int,
        backoff_seconds: float,
    ) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if retryable and backoff_seconds > 0:
                next_retry_expr = f"datetime('now', '+{int(backoff_seconds)} seconds')"
                cursor = conn.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'FAILED',
                        retryable = ?,
                        last_error = ?,
                        retry_count = ?,
                        next_retry_at = {next_retry_expr},
                        worker_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status IN ('RUNNING', 'CLAIMED')
                    """,
                    (int(retryable), error_message, retry_count, job_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'FAILED',
                        retryable = ?,
                        last_error = ?,
                        retry_count = ?,
                        next_retry_at = NULL,
                        worker_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status IN ('RUNNING', 'CLAIMED')
                    """,
                    (int(retryable), error_message, retry_count, job_id),
                )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def manual_retry(self, job_id: int) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'FAILED',
                    retryable = 1,
                    last_error = NULL,
                    next_retry_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'FAILED'
                """,
                (job_id,),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def release(self, job_id: int) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'PENDING', worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'CLAIMED'
                """,
                (job_id,),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def requeue(self, job_id: int) -> JobModel | None:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'PENDING', worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'FAILED'
                """,
                (job_id,),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def requeue_worker(self, worker_id: str) -> list[JobModel]:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id FROM jobs WHERE worker_id = ? AND status IN ('CLAIMED', 'RUNNING')",
                (worker_id,),
            ).fetchall()
            if not rows:
                return []
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE jobs
                SET status = 'PENDING', worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND worker_id = ?
                """,
                (*ids, worker_id),
            )
            updated_rows = conn.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return [self._to_model(r) for r in updated_rows]

    def _to_model(self, row: sqlite3.Row) -> JobModel:
        return JobModel(
            id=row["id"],
            name=row["name"],
            status=JobStatus(row["status"]),
            payload=row["payload"],
            worker_id=row["worker_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claim_count=row["claim_count"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            next_retry_at=row["next_retry_at"],
            last_error=row["last_error"],
            retryable=bool(row["retryable"]),
        )
