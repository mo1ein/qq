import asyncio
import contextlib
import logging
import os
import random

from app.model.job_models import JobModel, JobStatus
from app.repository.database import claim_lock
from app.repository.job_repository import JobRepository
from app.utils.util import FailureSimulator, classify_error, compute_backoff_delay

logger = logging.getLogger(__name__)

WORKER_ID = f"worker-{os.getpid()}"
MAX_CONCURRENT = 5
CLAIM_TIMEOUT_MINUTES = 5
POLL_INTERVAL = 0.5
MAX_POLL_INTERVAL = 5.0


class Worker:
    def __init__(self, repo: JobRepository, poll_interval: float = POLL_INTERVAL):
        self.repo = repo
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._active: set[asyncio.Task] = set()
        self._current_poll = poll_interval

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Worker {WORKER_ID} started (max concurrent={MAX_CONCURRENT})")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        for t in self._active:
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        logger.info("Worker shut down")

    async def _loop(self) -> None:
        recover_counter = 0
        while True:
            try:
                if recover_counter % 600 == 0:
                    recovered = await asyncio.to_thread(
                        self.repo.recover_stuck_jobs,
                        timeout_minutes=CLAIM_TIMEOUT_MINUTES,
                    )
                    if recovered:
                        logger.warning(f"Recovered {recovered} stuck job(s)")
                recover_counter += 1

                job = await self._claim_next_async()
                if job is not None:
                    self._current_poll = self.poll_interval
                    task = asyncio.create_task(self._run(job))
                    self._active.add(task)
                    task.add_done_callback(self._active.discard)
                else:
                    self._current_poll = min(self._current_poll * 2, MAX_POLL_INTERVAL)
                    await asyncio.sleep(self._current_poll)

            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                for t in self._active:
                    t.cancel()
                break
            except Exception as exc:
                logger.error(f"Worker loop error: {exc}", exc_info=True)
                await asyncio.sleep(1)

    async def _claim_next_async(self) -> JobModel | None:
        return await asyncio.to_thread(self._claim_next)

    def _claim_next(self) -> JobModel | None:
        with claim_lock:
            job = self.repo.claim_next_pending(WORKER_ID)
            if job is None:
                job = self.repo.claim_next_retryable(WORKER_ID)

        if job:
            logger.info(f"Claimed job {job.id} ({job.name})")
        return job

    async def _run(self, job: JobModel) -> None:
        async with self._semaphore:
            await self._execute(job)

    async def _execute(self, job: JobModel) -> None:
        job_id = job.id
        worker_id = job.worker_id

        started = await asyncio.to_thread(
            self.repo.update_status,
            job_id,
            JobStatus.RUNNING,
            expected_status=JobStatus.CLAIMED,
            expected_worker_id=worker_id,
        )
        if not started:
            logger.warning(f"Job {job_id} no longer owned by us — skipping")
            return

        try:
            await self._do_work(job)

            await asyncio.to_thread(
                self.repo.update_status,
                job_id,
                JobStatus.COMPLETED,
                expected_status=JobStatus.RUNNING,
                expected_worker_id=worker_id,
            )
            logger.info(f"Job {job_id} COMPLETED")

        except Exception as exc:
            await asyncio.to_thread(self._handle_failure, job, exc)

    def _handle_failure(self, job: JobModel, exc: Exception) -> None:
        is_retryable = classify_error(exc) and (job.retry_count < job.max_retries)
        new_retry_count = job.retry_count + 1 if is_retryable else job.retry_count
        backoff_seconds = compute_backoff_delay(job.retry_count) if is_retryable else 0

        self.repo.fail_job(
            job.id,
            retryable=is_retryable,
            error_message=str(exc)[:500],
            retry_count=new_retry_count,
            backoff_seconds=backoff_seconds,
        )

        if is_retryable:
            logger.warning(
                f"Job {job.id} FAILED (retryable) "
                f"attempt {new_retry_count}/{job.max_retries}, "
                f"next retry in ~{backoff_seconds:.1f}s"
            )
        else:
            logger.error(f"Job {job.id} FAILED permanently: {exc}")

    async def _do_work(self, job: JobModel) -> None:
        await asyncio.sleep(random.uniform(0.3, 1.2))
        # TODO: can do real work like running command...

        simulator = FailureSimulator()
        simulator.simulate(success_rate=0.7)
