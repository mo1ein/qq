import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repository.database import Database
from app.repository.dependencies import set_database


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    test_db = Database(str(tmp_path / "test.db"))
    test_db.init()
    set_database(test_db)
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def create_job(
    client: TestClient,
    name: str = "test-job",
    payload: dict | None = None,
    max_retries: int | None = None,
) -> dict:
    body: dict = {"name": name, "payload": payload}
    if max_retries is not None:
        body["max_retries"] = max_retries
    resp = client.post("/jobs", json=body)
    assert resp.status_code == 201
    return resp.json()


def claim_job(client: TestClient, job_id: int) -> dict:
    resp = client.post(f"/jobs/{job_id}/claim")
    assert resp.status_code == 200
    return resp.json()


class TestCreateJob:
    def test_create_job(self, client):
        job = create_job(client, "email-send", {"to": "user@example.com"})
        assert job["name"] == "email-send"
        assert job["status"] == "PENDING"
        assert job["payload"] == {"to": "user@example.com"}

    def test_created_job_has_null_worker_id(self, client):
        job = create_job(client, "no-worker")
        assert "worker_id" not in job

    def test_create_empty_name_fails(self, client):
        resp = client.post("/jobs", json={"name": ""})
        assert resp.status_code == 422


class TestClaimJob:
    def test_successful_claim(self, client):
        job = create_job(client, "task-1")
        claimed = claim_job(client, job["id"])
        assert claimed["status"] == "CLAIMED"
        assert claimed["worker_id"] is not None
        assert len(claimed["worker_id"]) == 12
        assert claimed["claim_count"] == 1

    def test_claim_nonexistent_job_returns_404(self, client):
        resp = client.post("/jobs/999/claim")
        assert resp.status_code == 404

    def test_claim_already_claimed_job_returns_404(self, client):
        job = create_job(client, "task-1")
        claim_job(client, job["id"])
        resp = client.post(f"/jobs/{job['id']}/claim")
        assert resp.status_code == 404

    def test_can_claim_different_jobs(self, client):
        job1 = create_job(client, "task-1")
        job2 = create_job(client, "task-2")

        claimed1 = claim_job(client, job1["id"])
        claimed2 = claim_job(client, job2["id"])

        assert claimed1["name"] == "task-1"
        assert claimed2["name"] == "task-2"
        assert claimed1["worker_id"] != claimed2["worker_id"]

    def test_worker_id_is_unique(self, client):
        job1 = create_job(client, "task-1")
        job2 = create_job(client, "task-2")

        claimed1 = claim_job(client, job1["id"])
        claimed2 = claim_job(client, job2["id"])
        assert claimed1["worker_id"] != claimed2["worker_id"]


class TestStateTransitions:
    def test_full_lifecycle(self, client):
        job = create_job(client, "lifecycle")
        jid = job["id"]

        claimed = claim_job(client, jid)
        assert claimed["status"] == "CLAIMED"
        worker_id = claimed["worker_id"]

        running = client.post(
            f"/jobs/{jid}/start", json={"worker_id": worker_id}
        ).json()
        assert running["status"] == "RUNNING"

        done = client.post(
            f"/jobs/{jid}/complete", json={"worker_id": worker_id}
        ).json()
        assert done["status"] == "COMPLETED"

    def test_invalid_transition_rejected(self, client):
        job = create_job(client, "bad-transition")
        resp = client.post(f"/jobs/{job['id']}/complete", json={"worker_id": "w1"})
        assert resp.status_code == 409

    def test_wrong_worker_cannot_start(self, client):
        job = create_job(client, "wrong-worker")
        claim_job(client, job["id"])
        resp = client.post(
            f"/jobs/{job['id']}/start", json={"worker_id": "wrong-worker"}
        )
        assert resp.status_code == 409

    def test_claimed_to_failed(self, client):
        job = create_job(client, "fail-claim")
        claimed = claim_job(client, job["id"])
        failed = client.post(
            f"/jobs/{job['id']}/fail",
            json={"worker_id": claimed["worker_id"], "retryable": False},
        ).json()
        assert failed["status"] == "FAILED"

    def test_failed_cannot_be_started(self, client):
        job = create_job(client, "requeue")
        claimed = claim_job(client, job["id"])
        client.post(f"/jobs/{job['id']}/fail", json={"worker_id": claimed["worker_id"]})

        resp = client.post(
            f"/jobs/{job['id']}/start", json={"worker_id": claimed["worker_id"]}
        )
        assert resp.status_code == 409


class TestWorkerFailure:
    def test_requeue_worker_jobs(self, client):
        job1 = create_job(client, "t1")
        job2 = create_job(client, "t2")

        claimed1 = claim_job(client, job1["id"])
        worker_id = claimed1["worker_id"]

        resp = client.post(f"/jobs/requeue/{worker_id}")
        assert resp.status_code == 200
        assert resp.json()["requeued"] == 1

        j1 = client.get(f"/jobs/{job1['id']}").json()
        j2 = client.get(f"/jobs/{job2['id']}").json()
        assert j1["status"] == "PENDING"
        assert "worker_id" not in j1
        assert j2["status"] == "PENDING"

    def test_requeue_only_affected_worker_jobs(self, client):
        job1 = create_job(client, "t1")
        job2 = create_job(client, "t2")

        claimed1 = claim_job(client, job1["id"])
        claimed2 = claim_job(client, job2["id"])

        client.post(f"/jobs/requeue/{claimed1['worker_id']}")

        j1 = client.get(f"/jobs/{job1['id']}").json()
        j2 = client.get(f"/jobs/{job2['id']}").json()
        assert j1["status"] == "PENDING"
        assert j2["status"] == "CLAIMED"
        assert j2["worker_id"] == claimed2["worker_id"]


class TestListing:
    def test_list_all(self, client):
        create_job(client, "a")
        create_job(client, "b")
        resp = client.get("/jobs")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_by_status(self, client):
        job1 = create_job(client, "a")
        create_job(client, "b")
        claim_job(client, job1["id"])

        resp = client.get("/jobs?status=PENDING")
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

        resp = client.get("/jobs?status=CLAIMED")
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    def test_pagination(self, client):
        for i in range(5):
            create_job(client, f"job-{i}")

        resp = client.get("/jobs?limit=2&offset=0")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

        resp = client.get("/jobs?limit=2&offset=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["offset"] == 2


class TestRelease:
    def test_release_claimed_job(self, client):
        job = create_job(client, "release-test")
        claimed = claim_job(client, job["id"])
        resp = client.post(
            f"/jobs/{job['id']}/release", json={"worker_id": claimed["worker_id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "PENDING"
        assert "worker_id" not in resp.json()

    def test_cannot_release_other_workers_job(self, client):
        job = create_job(client, "no-release")
        claim_job(client, job["id"])
        resp = client.post(
            f"/jobs/{job['id']}/release", json={"worker_id": "wrong-worker"}
        )
        assert resp.status_code == 409


class TestConcurrentClaim:
    def test_two_workers_cannot_claim_same_job_simultaneously(self, client):
        job = create_job(client, "concurrent-test")
        job_id = job["id"]

        results = []

        def try_claim():
            resp = client.post(f"/jobs/{job_id}/claim")
            results.append(resp.status_code)

        threads = [threading.Thread(target=try_claim) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1
        assert results.count(404) == 1

        final = client.get(f"/jobs/{job_id}").json()
        assert final["status"] == "CLAIMED"
        assert final["claim_count"] == 1

    def test_concurrent_claim_data_integrity(self, client):
        job = create_job(client, "race-test")
        job_id = job["id"]
        assert "worker_id" not in job

        results = []
        worker_ids = []

        def try_claim():
            resp = client.post(f"/jobs/{job_id}/claim")
            results.append(resp.status_code)
            if resp.status_code == 200:
                worker_ids.append(resp.json()["worker_id"])

        threads = [threading.Thread(target=try_claim) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1
        assert len(worker_ids) == 1
        assert worker_ids[0] is not None
        assert len(worker_ids[0]) == 12

        final = client.get(f"/jobs/{job_id}").json()
        assert final["status"] == "CLAIMED"
        assert final["worker_id"] == worker_ids[0]
        assert final["claim_count"] == 1

    def test_concurrent_claims_on_different_jobs_both_succeed(self, client):
        job1 = create_job(client, "job-1")
        job2 = create_job(client, "job-2")

        results = []

        def claim(jid):
            resp = client.post(f"/jobs/{jid}/claim")
            results.append(resp.status_code)

        t1 = threading.Thread(target=claim, args=(job1["id"],))
        t2 = threading.Thread(target=claim, args=(job2["id"],))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results.count(200) == 2

        j1 = client.get(f"/jobs/{job1['id']}").json()
        j2 = client.get(f"/jobs/{job2['id']}").json()
        assert j1["status"] == "CLAIMED"
        assert j2["status"] == "CLAIMED"
        assert j1["worker_id"] != j2["worker_id"]


class TestRetryPolicy:
    def test_retryable_failure_sets_failed_with_retry_info(self, client):
        job = create_job(client, "retry-test")
        claimed = claim_job(client, job["id"])

        failed = client.post(
            f"/jobs/{job['id']}/fail",
            json={
                "worker_id": claimed["worker_id"],
                "retryable": True,
                "error_message": "timeout",
            },
        ).json()
        assert failed["status"] == "FAILED"
        assert failed["retry_count"] == 1
        assert failed["last_error"] == "timeout"
        assert failed["next_retry_at"] is not None
        assert failed["retryable"] is True

    def test_non_retryable_failure_goes_to_failed(self, client):
        job = create_job(client, "no-retry-test")
        claimed = claim_job(client, job["id"])

        failed = client.post(
            f"/jobs/{job['id']}/fail",
            json={
                "worker_id": claimed["worker_id"],
                "retryable": False,
                "error_message": "invalid input",
            },
        ).json()
        assert failed["status"] == "FAILED"
        assert failed["retry_count"] == 0
        assert failed["last_error"] == "invalid input"
        assert failed["retryable"] is False

    def test_retry_exhaustion_goes_to_failed(self, client):
        job = create_job(client, "exhaust-test", max_retries=3)

        for i in range(4):
            claimed = claim_job(client, job["id"])
            client.post(
                f"/jobs/{job['id']}/start",
                json={"worker_id": claimed["worker_id"]},
            )
            failed = client.post(
                f"/jobs/{job['id']}/fail",
                json={
                    "worker_id": claimed["worker_id"],
                    "retryable": True,
                    "error_message": f"error {i}",
                },
            ).json()
            if i < 3:
                client.post(f"/jobs/{job['id']}/requeue")

        assert failed["status"] == "FAILED"
        assert failed["retry_count"] == 3
        assert failed["last_error"] == "error 3"
        assert failed["retryable"] is False

    def test_manual_retry_resets_failed_job(self, client):
        job = create_job(client, "retry-manual")
        claimed = claim_job(client, job["id"])

        client.post(
            f"/jobs/{job['id']}/fail",
            json={
                "worker_id": claimed["worker_id"],
                "retryable": True,
                "error_message": "temp error",
            },
        )

        job_data = client.get(f"/jobs/{job['id']}").json()
        assert job_data["status"] == "FAILED"
        assert job_data["last_error"] == "temp error"

        resp = client.post(f"/jobs/{job['id']}/retry")
        assert resp.status_code == 200
        retried = resp.json()
        assert retried["status"] == "FAILED"
        assert retried["retryable"] is True
        assert retried.get("last_error") is None
        assert retried["next_retry_at"] is not None

        resp = client.post(f"/jobs/{job['id']}/requeue")
        assert resp.status_code == 200
        requeued = resp.json()
        assert requeued["status"] == "PENDING"

    def test_retry_count_increments(self, client):
        job = create_job(client, "count-test", max_retries=5)

        for i in range(2):
            claimed = claim_job(client, job["id"])
            client.post(
                f"/jobs/{job['id']}/start",
                json={"worker_id": claimed["worker_id"]},
            )
            client.post(
                f"/jobs/{job['id']}/fail",
                json={"worker_id": claimed["worker_id"], "retryable": True},
            )
            if i < 1:
                client.post(f"/jobs/{job['id']}/requeue")

        job_data = client.get(f"/jobs/{job['id']}").json()
        assert job_data["retry_count"] == 2

    def test_failed_job_cannot_be_failed_again(self, client):
        job = create_job(client, "double-fail")
        claimed = claim_job(client, job["id"])

        client.post(
            f"/jobs/{job['id']}/fail",
            json={"worker_id": claimed["worker_id"], "retryable": False},
        )

        resp = client.post(
            f"/jobs/{job['id']}/fail",
            json={"worker_id": claimed["worker_id"], "retryable": False},
        )
        assert resp.status_code == 409


class TestUIPayload:
    def test_list_includes_retry_fields(self, client):
        create_job(client, "ui-list-test")
        resp = client.get("/jobs")
        data = resp.json()
        assert data["total"] == 1
        j = data["items"][0]
        assert "retry_count" in j
        assert "retryable" in j
        assert "claim_count" in j

    def test_detail_includes_retry_fields(self, client):
        job = create_job(client, "ui-detail-test")
        resp = client.get(f"/jobs/{job['id']}")
        j = resp.json()
        assert "retry_count" in j
        assert "retryable" in j
        assert "max_retries" in j

    def test_retry_fields_populated_after_failure(self, client):
        job = create_job(client, "ui-retry-test")
        claimed = claim_job(client, job["id"])
        client.post(
            f"/jobs/{job['id']}/fail",
            json={
                "worker_id": claimed["worker_id"],
                "retryable": True,
                "error_message": "test error",
            },
        )
        resp = client.get(f"/jobs/{job['id']}")
        j = resp.json()
        assert j["retry_count"] == 1
        assert j["last_error"] == "test error"
        assert j["retryable"] is True
        assert j["next_retry_at"] is not None
