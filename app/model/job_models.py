import enum
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

MAX_RETRIES = 5


class JobStatus(enum.StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.CLAIMED},
    JobStatus.CLAIMED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.PENDING},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.PENDING},
}


class JobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    name: str
    status: JobStatus = JobStatus.PENDING
    payload: dict[str, Any] | None = None
    worker_id: str | None = None
    claim_count: int = 0
    retry_count: int = 0
    max_retries: int = MAX_RETRIES
    next_retry_at: str | None = None
    last_error: str | None = None
    retryable: bool = True
    created_at: str = ""
    updated_at: str = ""

    @field_validator("payload", mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
        if isinstance(value, dict):
            return value
        return None
