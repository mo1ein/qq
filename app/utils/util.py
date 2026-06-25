from datetime import datetime, timezone
from dataclasses import dataclass

import random


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


BASE_DELAY = 1.0
MAX_DELAY = 3600.0


def compute_backoff_delay(attempt: int) -> float:
    jitter = random.uniform(0, BASE_DELAY)
    delay = BASE_DELAY * (2**attempt) + jitter
    return min(delay, MAX_DELAY)


def classify_error(exc: Exception) -> bool:
    retryable_types = (TimeoutError, ConnectionError, OSError)
    non_retryable_types = (ValueError, KeyError, TypeError)
    if isinstance(exc, non_retryable_types):
        return False
    if isinstance(exc, retryable_types):
        return True
    return True


# TODO: use pydantic
@dataclass
class FailureScenario:
    name: str
    exception_type: type
    message: str
    weight: int = 1


DEFAULT_FAILURE_SCENARIOS = [
    FailureScenario(
        "connection_timeout", TimeoutError, "Connection timed out", weight=3
    ),
    FailureScenario(
        "connection_refused", ConnectionError, "Connection refused", weight=2
    ),
    FailureScenario(
        "connection_reset", ConnectionError, "Connection reset by peer", weight=2
    ),
    FailureScenario("dns_failure", OSError, "DNS resolution failed", weight=1),
    FailureScenario("io_error", OSError, "I/O error occurred", weight=1),
    FailureScenario(
        "permission_denied", PermissionError, "Permission denied", weight=1
    ),
    FailureScenario(
        "file_not_found", FileNotFoundError, "Required file not found", weight=1
    ),
    FailureScenario("memory_error", MemoryError, "Out of memory", weight=1),
    FailureScenario("value_error", ValueError, "Invalid value received", weight=1),
    FailureScenario("type_error", TypeError, "Unexpected type", weight=1),
]


class FailureSimulator:
    def __init__(self, scenarios: list[FailureScenario] | None = None):
        self.scenarios = scenarios or DEFAULT_FAILURE_SCENARIOS
        self._build_weighted_pool()

    def _build_weighted_pool(self) -> None:
        self._pool: list[FailureScenario] = []
        for scenario in self.scenarios:
            self._pool.extend([scenario] * scenario.weight)

    def simulate(self, success_rate: float = 0.7) -> None:
        if random.random() < success_rate:
            return
        scenario = random.choice(self._pool)
        raise scenario.exception_type(scenario.message)
