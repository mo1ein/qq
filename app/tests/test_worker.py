import asyncio
import json
import os
import signal
import subprocess
import time
import urllib.request
from collections import Counter

import pytest

from app.model.job_models import JobModel, JobStatus
from app.repository.database import Database
from app.repository.dependencies import set_database
from app.repository.job_repository import JobRepository
from app.service.worker import WORKER_ID, Worker


@pytest.fixture
def repo(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init()
    set_database(db)
    return JobRepository(db)


class TestWorkerLoop:
    def test_worker_claims_and_processes_jobs(self, repo):
        for i in range(10):
            repo.create(JobModel(name=f"job-{i}"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(15)
            await worker.stop()

        asyncio.run(run())

        jobs = repo.list_all(limit=1000)
        assert len(jobs) == 10
        statuses = Counter(j.status for j in jobs)
        assert statuses.get(JobStatus.PENDING, 0) == 0
        assert statuses.get(JobStatus.CLAIMED, 0) == 0
        assert statuses.get(JobStatus.RUNNING, 0) == 0

    def test_worker_worker_id_matches_pid(self, repo):
        repo.create(JobModel(name="pid-check"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(2)
            await worker.stop()

        asyncio.run(run())

        job = repo.get(1)
        assert job.claim_count >= 1
        if job.worker_id:
            assert job.worker_id == f"worker-{os.getpid()}"

    def test_worker_retries_failures(self, repo):
        for i in range(10):
            repo.create(JobModel(name=f"job-{i}"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(15)
            await worker.stop()

        asyncio.run(run())

        jobs = repo.list_all(limit=1000)
        completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
        failed = [j for j in jobs if j.status == JobStatus.FAILED]
        assert len(completed) + len(failed) == 10
        for j in jobs:
            assert j.last_error is not None or j.status == JobStatus.COMPLETED

    def test_worker_all_jobs_resolved(self, repo):
        for i in range(10):
            repo.create(JobModel(name=f"job-{i}"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(15)
            await worker.stop()

        asyncio.run(run())

        jobs = repo.list_all(limit=1000)
        unresolved = [
            j for j in jobs if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        assert len(unresolved) == 0


class TestInterviewDemo:
    def test_full_lifecycle_demo(self, repo):
        for i in range(15):
            repo.create(JobModel(name=f"demo-job-{i}"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(10)
            await worker.stop()

        asyncio.run(run())

        jobs = repo.list_all(limit=1000)
        assert len(jobs) == 15

        completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
        failed = [j for j in jobs if j.status == JobStatus.FAILED]

        assert len(completed) + len(failed) == 15
        assert len(completed) > 0

    def test_pressure_50_jobs(self, repo):
        for i in range(50):
            repo.create(JobModel(name=f"pressure-{i}"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(20)
            await worker.stop()

        asyncio.run(run())

        jobs = repo.list_all()
        assert len(jobs) == 50
        unresolved = [
            j for j in jobs if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        assert len(unresolved) == 0

    def test_concurrent_workers_divide_work(self, repo):
        for i in range(15):
            repo.create(JobModel(name=f"job-{i}"))

        w1 = Worker(repo, poll_interval=0.01)
        w2 = Worker(repo, poll_interval=0.01)
        w3 = Worker(repo, poll_interval=0.01)

        async def run():
            w1.start()
            w2.start()
            w3.start()
            await asyncio.sleep(10)
            await w1.stop()
            await w2.stop()
            await w3.stop()

        asyncio.run(run())

        jobs = repo.list_all()
        unresolved = [
            j for j in jobs if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        assert len(unresolved) == 0

        completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
        for j in completed:
            assert j.worker_id == WORKER_ID
            assert j.claim_count >= 1

    def test_concurrent_pressure_5_workers_30_jobs(self, repo):
        for i in range(30):
            repo.create(JobModel(name=f"p-{i}"))

        workers = [Worker(repo, poll_interval=0.01) for _ in range(5)]

        async def run():
            for w in workers:
                w.start()
            await asyncio.sleep(15)
            for w in workers:
                await w.stop()

        asyncio.run(run())

        jobs = repo.list_all()
        assert len(jobs) == 30
        unresolved = [
            j for j in jobs if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        assert len(unresolved) == 0

    def test_worker_recover_stuck_jobs(self, repo):
        job = repo.create(JobModel(name="stuck-job"))
        repo.update_status(
            job.id,
            JobStatus.RUNNING,
            expected_status=JobStatus.PENDING,
        )

        conn = repo.db.get_connection()
        conn.execute(
            "UPDATE jobs SET updated_at = datetime('now', '-10 minutes') WHERE id = ?",
            (job.id,),
        )
        conn.commit()
        conn.close()

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(2)
            await worker.stop()

        asyncio.run(run())

        recovered = repo.get(job.id)
        assert recovered.claim_count >= 1
        assert recovered.status in (JobStatus.COMPLETED, JobStatus.FAILED)

    def test_timestamps_are_set_by_db(self, repo):
        repo.create(JobModel(name="ts-test"))

        worker = Worker(repo, poll_interval=0.01)

        async def run():
            worker.start()
            await asyncio.sleep(2)
            await worker.stop()

        asyncio.run(run())

        job = repo.get(1)
        assert job.created_at is not None
        assert job.created_at != ""
        assert job.updated_at is not None
        assert job.updated_at != ""
        assert job.created_at <= job.updated_at


def _wait_for_server(url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _api(base, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


class TestUvicornWorkers:
    def test_workers_get_unique_worker_ids(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        port = 18234

        env = os.environ.copy()
        env["DB_PATH"] = db_path
        env["PORT"] = str(port)

        proc = subprocess.Popen(
            [
                "uvicorn",
                "app.main:app",
                "--workers",
                "4",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            base = f"http://127.0.0.1:{port}"
            assert _wait_for_server(f"{base}/health"), "Server did not start"

            for i in range(8):
                _api(base, "/jobs", {"name": f"job-{i}"})

            deadline = time.time() + 30
            while time.time() < deadline:
                data = _api(base, "/jobs")
                jobs = data.get("items", [])
                pending = [j for j in jobs if j["status"] == "PENDING"]
                if len(pending) == 0:
                    break
                time.sleep(0.5)

            data = _api(base, "/jobs")
            jobs = data.get("items", [])
            worker_ids = [j["worker_id"] for j in jobs if j.get("worker_id")]

            assert len(worker_ids) == 8, (
                f"Expected 8 claimed jobs, got {len(worker_ids)}"
            )
            unique_ids = set(worker_ids)
            assert len(unique_ids) >= 2, (
                f"Expected multiple unique worker_ids, got {unique_ids}"
            )

            pids = set()
            for wid in worker_ids:
                assert wid.startswith("worker-")
                pid = int(wid.split("-")[1])
                assert pid > 0
                pids.add(pid)
            assert len(pids) >= 2, f"Expected multiple unique PIDs, got {pids}"

        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
