# Job Queue System

A job queue built with **FastAPI + SQLite** where background workers poll, claim, and execute jobs atomically. No message broker needed — just a database and smart SQL.

![Dashboard](dashboard.png)

## How It Works (TL;DR)

1. **API** creates a job → stored in SQLite as `PENDING`
2. **Worker** runs a loop: every 500ms it polls the database for the next available job
3. Worker **claims** the job atomically (`BEGIN IMMEDIATE` + `WHERE status = 'PENDING'`) — only one worker wins
4. Worker **executes** the job, then marks it `COMPLETED` or `FAILED`
5. Failed jobs are **retried** with exponential backoff, up to `max_retries`

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DB
    participant Worker

    Client->>API: POST /jobs {name: "send-email"}
    API->>DB: INSERT INTO jobs (status=PENDING)
    API-->>Client: 201 Created

    loop Every 500ms
        Worker->>DB: BEGIN IMMEDIATE
        Worker->>DB: SELECT ... WHERE status='PENDING' LIMIT 1
        DB-->>Worker: row found
        Worker->>DB: UPDATE SET status=CLAIMED, worker_id=?
        Worker->>DB: COMMIT
    end

    Worker->>Worker: Do the work (API call, compute, etc.)
    Worker->>DB: UPDATE SET status=COMPLETED
```

## Worker Loop — Step by Step

The `Worker` class runs inside the FastAPI process as an **asyncio task**. It does four things in a loop:

```mermaid
flowchart TD
    START([Loop Start]) --> RECOVER{Recover stuck jobs?}
    RECOVER -->|Every 30s| RS[recover_stuck_jobs<br/>Mark stuck jobs as FAILED]
    RECOVER -->|Skip| CLAIM
    RS --> CLAIM
    CLAIM[Claim next job] --> FOUND{Job found?}
    FOUND -->|Yes| SPAWN[Spawn async task<br/>to execute job]
    FOUND -->|No| SLEEP[Sleep 500ms]
    SPAWN --> START
    SLEEP --> START
```

### The 4 Key Operations

| Step | What Happens | SQL |
|------|-------------|-----|
| **Recover** | Jobs stuck in CLAIMED/RUNNING for >5min are marked FAILED (worker probably died) | `UPDATE ... SET status='FAILED' WHERE status IN ('CLAIMED','RUNNING') AND updated_at < cutoff` |
| **Claim** | Pick the oldest PENDING job and atomically set it to CLAIMED | `BEGIN IMMEDIATE` → `SELECT ... WHERE status='PENDING'` → `UPDATE ... SET status='CLAIMED'` → `COMMIT` |
| **Execute** | Run the actual job logic (API call, data processing, etc.) | Application code |
| **Finish** | Mark job COMPLETED or FAILED (with retry logic) | `UPDATE ... SET status='COMPLETED'` or `UPDATE ... SET status='FAILED'` |

## Concurrency — How Double-Claim Is Prevented

Two layers of protection ensure a job is never claimed by two workers:

```mermaid
flowchart LR
    W1[Worker A] -->|acquire| LOCK[claim_lock<br/>threading.Lock]
    W2[Worker B] -->|waiting...| LOCK
    LOCK -->|granted| W1
    W1 --> SQL[BEGIN IMMEDIATE<br/>+ UPDATE WHERE status='PENDING']
    SQL -->|rowcount=1| OK[Job claimed ✓]
    SQL -->|rowcount=0| FAIL[Already claimed ✗]
    W1 -->|release| LOCK
    LOCK -->|granted| W2
    W2 --> SQL2[UPDATE WHERE status='PENDING']
    SQL2 -->|rowcount=0| FAIL2[Already claimed ✗]
```

**Layer 1 — Python `threading.Lock`:** Only one thread can enter claim logic at a time within a single process.

**Layer 2 — SQLite `BEGIN IMMEDIATE`:** Acquires a write lock at the database level. Even across multiple processes (e.g., `uvicorn --workers 4`), SQLite serializes write transactions, so the `WHERE status = 'PENDING'` condition guarantees exactly one winner.

## State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING           : Create job
    PENDING --> CLAIMED       : Worker claims
    CLAIMED --> RUNNING       : Worker starts
    CLAIMED --> FAILED        : Worker fails
    CLAIMED --> PENDING       : Worker releases
    RUNNING --> COMPLETED     : Success
    RUNNING --> FAILED        : Failure
    FAILED --> PENDING        : Requeue / Retry
    COMPLETED --> [*]
```

| Status | worker_id | Meaning |
|--------|-----------|---------|
| `PENDING` | `NULL` | Waiting in the queue |
| `CLAIMED` | set | Reserved by a worker, about to start |
| `RUNNING` | set | Worker is executing the job |
| `COMPLETED` | remains | Done successfully |
| `FAILED` | `NULL` | Permanently failed or retryable failure |

> When a job transitions to `COMPLETED`, `retryable` is set to `0` and `next_retry_at` is set to `NULL` — completed jobs never carry stale retry state.

## Retry with Exponential Backoff

```mermaid
flowchart TD
    FAIL[Job fails] --> CLASSIFY{Error type?}
    CLASSIFY -->|Timeout, Connection, OS| RETRYABLE[Retryable]
    CLASSIFY -->|ValueError, TypeError| DEAD[Dead Letter]
    RETRYABLE --> COUNT{retry_count < max_retries?}
    COUNT -->|Yes| BACKOFF[Exponential Backoff<br/>delay = 1s × 2^attempt + jitter]
    COUNT -->|No| DEAD
    BACKOFF --> WAIT[Set next_retry_at<br/>Job becomes PENDING again]
    WAIT --> CLAIM2[Worker picks it up later]
    DEAD --> DONE[Stay FAILED forever]
```

**Formula:** `delay = min(1s × 2^attempt + random(0, 1s), 1 hour)`

| Attempt | Delay Range |
|---------|------------|
| 0 | 1–2s |
| 1 | 2–3s |
| 2 | 4–5s |
| 3 | 8–9s |
| 4 | 16–17s |

**Why jitter?** Prevents thundering herd — if 100 jobs fail at the same time, they won't all retry at the exact same moment.

## Worker Failure Handling

What happens when a worker process dies mid-job?

```mermaid
flowchart LR
    W[Worker dies 💀] -->|Jobs stuck in CLAIMED/RUNNING| RECOVER[recover_stuck_jobs]
    RECOVER -->|Every 30s, checks updated_at| TIMEOUT[Jobs older than 5 min]
    TIMEOUT --> RESET[Reset to FAILED with retryable=1]
    RESET --> RETRY[Worker reclaims and retries]
```

No external monitoring needed — the worker itself recovers stuck jobs from other (dead) workers every 30 seconds.

## Multi-Worker Setup

Each worker gets a unique ID based on its process ID: `worker-{PID}`.

```bash
# Single worker (default, with hot reload)
make run

# 4 worker processes
uv run uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000
```

```mermaid
flowchart TD
    subgraph uvicorn master
        M[master process — routes traffic]
    end
    subgraph workers
        W1[worker-1001<br/>polls + claims]
        W2[worker-1002<br/>polls + claims]
        W3[worker-1003<br/>polls + claims]
        W4[worker-1004<br/>polls + claims]
    end
    DB[(SQLite<br/>WAL mode)]
    M -->|forwards requests| DB
    W1 -->|BEGIN IMMEDIATE| DB
    W2 -->|BEGIN IMMEDIATE| DB
    W3 -->|BEGIN IMMEDIATE| DB
    W4 -->|BEGIN IMMEDIATE| DB
```

All 4 workers share the same SQLite database. `BEGIN IMMEDIATE` ensures only one can write at a time. Each worker picks different jobs because the claim query is `ORDER BY created_at ASC LIMIT 1` inside a serialized transaction.

## Quick Start

```bash
make install          # Install dependencies
make env              # create .env
make test             # Run all tests
make run              # Start server on port 8000
# or with multiple workers:
uv run uvicorn app.main:app --workers 4 --port 8000
```
