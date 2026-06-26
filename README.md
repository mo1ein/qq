# Job Queue System

A job queue built with **FastAPI + SQLite** where background workers poll, claim, and execute jobs atomically.

![Dashboard](dashboard.png)


## Quick Start

```bash
make install          # Install dependencies
make env              # create .env
make test             # Run all tests
make run              # Start server on port 8000
```

## Run with Docker

```bash
make docker-build     # Build image
make docker-run       # Run on port 8000
```

## Multi-Worker Setup

Each worker gets a unique ID: `worker-{PID}`.

```bash
# Single worker (default, with hot reload)
make run

# 4 worker processes
uv run uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000
```

All workers share the same SQLite database. `BEGIN IMMEDIATE` ensures only one can write at a time. Each worker picks different jobs via `ORDER BY created_at ASC LIMIT 1` inside a serialized transaction.


## How It Works

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

## Race condition — How Double-Claim Is Prevented

```mermaid
flowchart LR
    W1[Worker A] -->|acquire| LOCK[claim_lock]
    W2[Worker B] -->|waiting| LOCK
    LOCK -->|granted| W1
    W1 --> SQL[BEGIN IMMEDIATE + UPDATE]
    SQL -->|rowcount=1| OK[Claimed]
    SQL -->|rowcount=0| FAIL[Already claimed]
    W1 -->|release| LOCK
    LOCK -->|granted| W2
```

**Layer 1 — `threading.Lock`:** Prevents two threads in the same process from racing on the SELECT + UPDATE pair. Without it, Thread A could read a PENDING job, then Thread B reads the same row before A commits — both would try to claim it. The lock serializes access so only one thread at a time runs the claim sequence.

**Layer 2 — `BEGIN IMMEDIATE`:** SQLite's file-level write lock. When a transaction starts with `BEGIN IMMEDIATE`, it acquires a reserved write lock on the database file. Any other process trying to write will block until this transaction commits. This is what prevents two separate uvicorn worker processes from double-claiming the same job — even though they have separate `threading.Lock` instances.

**Together:** The `threading.Lock` is a fast in-process optimization that avoids unnecessary SQLite lock contention. The `BEGIN IMMEDIATE` is the real guarantee that works across processes. Even without the thread lock, the system would be correct — just slower under high concurrency.

## State Machine

```mermaid
flowchart LR
    START([Start]) --> PENDING[PENDING]
    PENDING -->|Worker claims| CLAIMED[CLAIMED]
    CLAIMED -->|Worker starts| RUNNING[RUNNING]
    CLAIMED -->|Worker fails| FAILED[FAILED]
    CLAIMED -->|Worker releases| PENDING
    RUNNING -->|Success| COMPLETED[COMPLETED]
    RUNNING -->|Failure| FAILED
    FAILED -->|Requeue / Retry| PENDING
    COMPLETED --> END([End])
```

Jobs flow through five states: `PENDING` → `CLAIMED` → `RUNNING` → `COMPLETED` or `FAILED`. Failed jobs can be retried back to `PENDING` via requeue or automatic retry with backoff. Each transition is enforced both at the service layer (`VALID_TRANSITIONS` dict) and at the database layer (`WHERE status = ?`).


## Worker Loop 

```mermaid
flowchart LR
    START([Loop]) --> RECOVER{Every 30s?}
    RECOVER -->|Yes| RS[Recover stuck jobs]
    RECOVER -->|No| CLAIM
    RS --> CLAIM
    CLAIM[Claim next job] --> FOUND{Found?}
    FOUND -->|Yes| EXEC[Execute]
    FOUND -->|No| SLEEP[Sleep 500ms]
    EXEC --> START
    SLEEP --> START
```

The worker runs an infinite loop: poll for a PENDING job, claim it atomically, execute it, and repeat. If no job is found, it sleeps and retries. Every 30 seconds it also checks for stuck jobs (CLAIMED/RUNNING for >5 min) and marks them FAILED so they can be retried.

## Retry with Exponential Backoff

```mermaid
flowchart LR
    FAIL[Job fails] --> CLASSIFY{Error type?}
    CLASSIFY -->|Retryable| BACKOFF[Backoff delay]
    CLASSIFY -->|Non-retryable| DEAD[Stay FAILED]
    BACKOFF --> COUNT{Retries left?}
    COUNT -->|Yes| WAIT[Wait, then retry]
    COUNT -->|No| DEAD
    WAIT --> CLAIM[Worker reclaims]
```

**Formula:** `delay = min(1s × 2^attempt + jitter, 1 hour)`

| Attempt | 0 | 1 | 2 | 3 | 4 |
|---------|---|---|---|---|---|
| Delay | 1–2s | 2–3s | 4–5s | 8–9s | 16–17s |

**Why jitter?** Prevents thundering herd — if 100 jobs fail simultaneously, they won't all retry at the exact same moment.


