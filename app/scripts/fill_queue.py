"""
Usage:
    uv run python scripts/fill_queue.py [--count 10] [--interval 2]
"""

import argparse
import json
import random
import sqlite3
import string
import time
from datetime import UTC, datetime

SAMPLE_JOBS = [
    (
        "send-email",
        lambda: {
            "to": f"{rand_name()}@example.com",
            "subject": random.choice(
                ["Welcome", "Invoice", "Password Reset", "Weekly Report"]
            ),
        },
    ),
    (
        "process-payment",
        lambda: {
            "amount": round(random.uniform(10, 500), 2),
            "currency": "USD",
            "order_id": f"ORD-{rand_id()}",
        },
    ),
    (
        "generate-report",
        lambda: {
            "type": random.choice(["monthly", "quarterly", "annual"]),
            "user_id": random.randint(1, 1000),
        },
    ),
    (
        "sync-inventory",
        lambda: {
            "warehouse": random.choice(["US-East", "EU-West", "APAC"]),
            "sku_count": random.randint(50, 500),
        },
    ),
    (
        "resize-image",
        lambda: {
            "image": f"photo-{rand_id()}.jpg",
            "width": random.choice([128, 256, 512, 1024]),
        },
    ),
    (
        "export-csv",
        lambda: {
            "table": random.choice(["users", "orders", "products"]),
            "format": "csv",
        },
    ),
    (
        "send-notification",
        lambda: {
            "user_id": random.randint(1, 1000),
            "channel": random.choice(["email", "sms", "push"]),
            "message": "Your order has shipped",
        },
    ),
    (
        "compute-analytics",
        lambda: {
            "metric": random.choice(["page_views", "signups", "revenue"]),
            "period": "last_7_days",
        },
    ),
    ("backup-database", lambda: {"db": "production", "compress": True}),
    (
        "validate-address",
        lambda: {
            "street": f"{random.randint(1, 999)} Main St",
            "city": random.choice(["NYC", "LA", "Chicago", "Houston"]),
            "zip": f"{random.randint(10000, 99999)}",
        },
    ),
]


def rand_id():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def rand_name():
    return random.choice(
        ["alice", "bob", "charlie", "diana", "eve", "frank", "grace", "henry"]
    )


def insert_jobs(db_path: str, count: int):
    conn = sqlite3.connect(db_path)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(count):
        name, payload_fn = random.choice(SAMPLE_JOBS)
        payload = json.dumps(payload_fn())
        conn.execute(
            """INSERT INTO jobs (name, status, payload, created_at, updated_at)
               VALUES (?, 'PENDING', ?, ?, ?)""",
            (name, payload, now, now),
        )
    conn.commit()
    conn.close()


def print_status(db_path: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    counts = {r[0]: r[1] for r in rows}
    parts = []
    for s in ["PENDING", "CLAIMED", "RUNNING", "COMPLETED", "FAILED"]:
        c = counts.get(s, 0)
        if c:
            parts.append(f"{s}={c}")
    print(
        f"  [{datetime.now().strftime('%H:%M:%S')}] total={total}  {'  '.join(parts)}"
    )


def main():
    parser = argparse.ArgumentParser(description="Fill job queue with sample jobs")
    parser.add_argument("--count", "-n", type=int, default=5, help="Jobs per batch")
    parser.add_argument(
        "--interval", "-i", type=float, default=2, help="Seconds between batches"
    )
    parser.add_argument(
        "--batches", "-b", type=int, default=0, help="Stop after N batches (0=infinite)"
    )
    args = parser.parse_args()

    # db_path = configs.DB_PATH
    db_path = "../../jobs.db"
    print(f"Inserting {args.count} jobs every {args.interval}s into {db_path}")
    print("Press Ctrl+C to stop\n")

    batch = 0
    try:
        while args.batches == 0 or batch < args.batches:
            insert_jobs(db_path, args.count)
            print_status(db_path)
            batch += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nDone. Inserted {batch * args.count} jobs total.")


if __name__ == "__main__":
    main()
