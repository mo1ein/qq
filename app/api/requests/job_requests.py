from typing import Optional

from pydantic import BaseModel, Field

from app.model.job_models import JobStatus, MAX_RETRIES


class CreateJobRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    payload: Optional[dict] = None
    max_retries: int = Field(default=MAX_RETRIES, ge=0, le=100)


class JobResponse(BaseModel):
    id: int
    name: str
    status: JobStatus
    payload: Optional[dict] = None
    worker_id: Optional[str] = None
    created_at: str
    updated_at: str
    claim_count: int = 0
    retry_count: int = 0
    max_retries: int = 5
    next_retry_at: Optional[str] = None
    last_error: Optional[str] = None
    retryable: bool = True


class JobListItem(BaseModel):
    id: int
    name: str
    status: JobStatus
    worker_id: Optional[str] = None
    created_at: str
    updated_at: str
    claim_count: int = 0
    retry_count: int = 0
    retryable: bool = True


class PaginatedJobsResponse(BaseModel):
    items: list[JobListItem]
    total: int
    limit: int
    offset: int


class WorkerRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=128)


class FailJobRequest(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=128)
    retryable: bool = True
    error_message: str | None = None
