from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.requests.job_requests import (
    CreateJobRequest,
    FailJobRequest,
    JobListItem,
    JobResponse,
    PaginatedJobsResponse,
    WorkerRequest,
)
from app.model.job_models import JobModel, JobStatus
from app.repository.database import get_job_service
from app.service.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/stats")
def get_stats(service: JobService = Depends(get_job_service)):
    counts = {}
    for status in JobStatus:
        _, total = service.list_jobs(status, limit=0, offset=0)
        counts[status.value] = total
    return counts


@router.post(
    "", status_code=201, response_model=JobResponse, response_model_exclude_none=True
)
def create_job(
    request: CreateJobRequest, service: JobService = Depends(get_job_service)
):
    job_model = JobModel(
        name=request.name,
        status=JobStatus.PENDING,
        payload=request.payload,
        max_retries=request.max_retries,
    )
    job = service.create_job(job_model)
    return JobResponse.model_validate(job.model_dump())


@router.get("", response_model=PaginatedJobsResponse)
def list_jobs(
    status: JobStatus | None = None,
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: JobService = Depends(get_job_service),
):
    jobs, total = service.list_jobs(status, limit=limit, offset=offset)
    items = [
        JobListItem(
            id=j.id,
            name=j.name,
            status=j.status,
            worker_id=j.worker_id,
            retry_count=j.retry_count,
            retryable=j.retryable,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )
        for j in jobs
    ]
    return PaginatedJobsResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse, response_model_exclude_none=True)
def get_job(job_id: int, service: JobService = Depends(get_job_service)):
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/claim", response_model=JobResponse, response_model_exclude_none=True
)
def claim_job(
    job_id: int,
    data: WorkerRequest | None = Body(None),
    service: JobService = Depends(get_job_service),
):
    worker_id = data.worker_id if data else None
    job = service.claim_job(job_id, worker_id=worker_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not pending")
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/start", response_model=JobResponse, response_model_exclude_none=True
)
def start_job(
    job_id: int, data: WorkerRequest, service: JobService = Depends(get_job_service)
):
    job = service.start_job(job_id, data.worker_id)
    if not job:
        raise HTTPException(
            status_code=409, detail="Job cannot be started by this worker"
        )
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/complete", response_model=JobResponse, response_model_exclude_none=True
)
def complete_job(
    job_id: int, data: WorkerRequest, service: JobService = Depends(get_job_service)
):
    job = service.complete_job(job_id, data.worker_id)
    if not job:
        raise HTTPException(
            status_code=409, detail="Job cannot be completed by this worker"
        )
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/fail", response_model=JobResponse, response_model_exclude_none=True
)
def fail_job(
    job_id: int, data: FailJobRequest, service: JobService = Depends(get_job_service)
):
    job = service.fail_job(
        job_id,
        data.worker_id,
        retryable=data.retryable,
        error_message=data.error_message,
    )
    if not job:
        raise HTTPException(
            status_code=409, detail="Job cannot be failed by this worker"
        )
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/release", response_model=JobResponse, response_model_exclude_none=True
)
def release_job(
    job_id: int, data: WorkerRequest, service: JobService = Depends(get_job_service)
):
    job = service.release_job(job_id, data.worker_id)
    if not job:
        raise HTTPException(
            status_code=409, detail="Job cannot be released by this worker"
        )
    return JobResponse.model_validate(job.model_dump())


@router.post("/requeue/{worker_id}")
def requeue_worker_jobs(worker_id: str, service: JobService = Depends(get_job_service)):
    jobs = service.requeue_worker_jobs(worker_id)
    return {
        "requeued": len(jobs),
        "jobs": [
            JobResponse.model_validate(j.model_dump()).model_dump(exclude_none=True)
            for j in jobs
        ],
    }


@router.post(
    "/{job_id}/requeue", response_model=JobResponse, response_model_exclude_none=True
)
def requeue_job(job_id: int, service: JobService = Depends(get_job_service)):
    job = service.requeue_job(job_id)
    if not job:
        raise HTTPException(status_code=409, detail="Job cannot be requeued")
    return JobResponse.model_validate(job.model_dump())


@router.post(
    "/{job_id}/retry",
    response_model=JobResponse,
    response_model_exclude_none=True,
)
def manual_retry_job(job_id: int, service: JobService = Depends(get_job_service)):
    job = service.manual_retry(job_id)
    if not job:
        raise HTTPException(status_code=409, detail="Job cannot be retried")
    return JobResponse.model_validate(job.model_dump())
