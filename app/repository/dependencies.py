from __future__ import annotations

from app.repository.database import Database
from app.repository.job_repository import JobRepository
from app.service.job_service import JobService

_db: Database | None = None
_repo: JobRepository | None = None
_service: JobService | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def get_job_repository() -> JobRepository:
    global _repo
    if _repo is None:
        _repo = JobRepository(get_database())
    return _repo


def get_job_service() -> JobService:
    global _service
    if _service is None:
        _service = JobService(get_job_repository())
    return _service


def set_database(db: Database) -> None:
    global _db, _repo, _service
    _db = db
    _repo = JobRepository(db)
    _service = JobService(_repo)
