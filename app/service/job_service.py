import uuid

from app.model.job_models import (
    JobModel,
    JobStatus,
    VALID_TRANSITIONS,
)
from app.repository.database import claim_lock
from app.repository.job_repository import JobRepository
from app.utils.util import compute_backoff_delay


DEFAULT_WORKER_PREFIX = "api-worker"


class JobService:
    def __init__(self, repo: JobRepository):
        self.repo = repo

    def _check_transition(self, current: JobStatus, target: JobStatus) -> bool:
        return target in VALID_TRANSITIONS.get(current, set())

    def create_job(self, job_model: JobModel) -> JobModel:
        created = self.repo.create(job_model)
        return JobModel.model_validate(created)

    def get_job(self, job_id: int) -> JobModel | None:
        model = self.repo.get(job_id)
        return JobModel.model_validate(model) if model else None

    def list_jobs(
        self,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobModel], int]:
        models = self.repo.list_all(status, limit=limit, offset=offset)
        total = self.repo.count_all(status)
        return [JobModel.model_validate(model) for model in models], total

    def claim_job(self, job_id: int, worker_id: str | None = None) -> JobModel | None:
        if worker_id is None:
            worker_id = f"{DEFAULT_WORKER_PREFIX}-{uuid.uuid4().hex[:8]}"
        with claim_lock:
            model = self.repo.claim_job(job_id, worker_id)
        return JobModel.model_validate(model) if model else None

    def start_job(self, job_id: int, worker_id: str) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model or model.worker_id != worker_id:
            return None
        if not self._check_transition(model.status, JobStatus.RUNNING):
            return None

        updated = self.repo.update_status(
            job_id,
            JobStatus.RUNNING,
            expected_status=JobStatus.CLAIMED,
            expected_worker_id=worker_id,
        )
        return JobModel.model_validate(updated) if updated else None

    def complete_job(self, job_id: int, worker_id: str) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model or model.worker_id != worker_id:
            return None
        if not self._check_transition(model.status, JobStatus.COMPLETED):
            return None

        updated = self.repo.update_status(
            job_id,
            JobStatus.COMPLETED,
            expected_status=JobStatus.RUNNING,
            expected_worker_id=worker_id,
        )
        return JobModel.model_validate(updated) if updated else None

    def fail_job(
        self,
        job_id: int,
        worker_id: str,
        retryable: bool = True,
        error_message: str | None = None,
    ) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model or model.worker_id != worker_id:
            return None
        if not self._check_transition(model.status, JobStatus.FAILED):
            return None

        is_retryable = retryable and model.retry_count < model.max_retries
        new_retry_count = model.retry_count + 1 if is_retryable else model.retry_count
        backoff_seconds = (
            compute_backoff_delay(model.retry_count) if is_retryable else 0
        )

        updated = self.repo.fail_job(
            job_id,
            retryable=is_retryable,
            error_message=error_message,
            retry_count=new_retry_count,
            backoff_seconds=backoff_seconds,
        )
        return JobModel.model_validate(updated) if updated else None

    def release_job(self, job_id: int, worker_id: str) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model or model.worker_id != worker_id:
            return None
        if not self._check_transition(model.status, JobStatus.PENDING):
            return None

        released = self.repo.release(job_id)
        return JobModel.model_validate(released) if released else None

    def requeue_worker_jobs(self, worker_id: str) -> list[JobModel]:
        models = self.repo.requeue_worker(worker_id)
        return [JobModel.model_validate(model) for model in models]

    def requeue_job(self, job_id: int) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model:
            return None
        if not self._check_transition(model.status, JobStatus.PENDING):
            return None

        requeued = self.repo.requeue(job_id)
        return JobModel.model_validate(requeued) if requeued else None

    def manual_retry(self, job_id: int) -> JobModel | None:
        model = self.repo.get(job_id)
        if not model:
            return None
        if model.status != JobStatus.FAILED:
            return None

        updated = self.repo.manual_retry(job_id)
        return JobModel.model_validate(updated) if updated else None
