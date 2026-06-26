
from pydantic import BaseModel, Field

from app.model.job_models import MAX_RETRIES, JobStatus


class CreateJobRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    payload: dict | None = None
    max_retries: int = Field(default=MAX_RETRIES, ge=0, le=100)


class JobResponse(BaseModel):
    id: int
    name: str
    status: JobStatus
    payload: dict | None = None
    worker_id: str | None = None
    claim_count: int = 0
    retry_count: int = 0
    max_retries: int = 5
    next_retry_at: str | None = None
    last_error: str | None = None
    retryable: bool = True
    created_at: str
    updated_at: str


class JobListItem(BaseModel):
    id: int
    name: str
    status: JobStatus
    worker_id: str | None = None
    claim_count: int = 0
    retry_count: int = 0
    retryable: bool = True
    created_at: str
    updated_at: str


class PaginatedJobsResponse(BaseModel):
    items: list[JobListItem]
    total: int
    limit: int
    offset: int


class WorkerRequest(BaseModel):
    worker_id: str | None = Field(default=None, max_length=128)


class FailJobRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=128)
    retryable: bool = True
    error_message: str | None = None
