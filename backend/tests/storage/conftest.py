"""Local test fixtures for M5 storage tests.

These fixtures stand in for M0 envelopes while the shared contract package is
absent. They are deliberately dumb carriers: production code must depend on the
M0 public interface, not on anything defined here.
"""

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if find_spec("agent_bridge") is None and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@dataclass
class FakeCommandEnvelope:
    scope_type: str
    scope_id: str
    command_id: str
    command_idempotency_key: str
    command_request_digest: str
    command_type: str = "TEST_COMMAND"
    issuer: str = "local-user"
    protocol_version: str = "0.5.1"
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None


@dataclass
class FakeCandidateEvent:
    scope_type: str
    scope_id: str
    event_id: str
    operation_id: str
    event_deduplication_key: str
    candidate_event_digest: str
    event_type: str = "TEST_RECORDED"
    actor: str = "m4"
    causation_id: str | None = None
    digest_algorithm_version: str = "sha256-canonical-json-v1"
    protocol_version: str = "0.5.1"
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = NOW
    integrity_ref: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None


def task_event(seq: int, **overrides: Any) -> FakeCandidateEvent:
    data: dict[str, Any] = {
        "scope_type": "TASK",
        "scope_id": "task-1",
        "event_id": f"event-{seq}",
        "operation_id": f"operation-{seq}",
        "event_deduplication_key": f"dedup-{seq}",
        "candidate_event_digest": f"digest-{seq}",
        "task_id": "task-1",
        "run_id": "run-1",
    }
    data.update(overrides)
    return FakeCandidateEvent(**data)


@pytest.fixture
def store(tmp_path: Path):
    from agent_bridge.storage.event_store import EventStore

    with EventStore(tmp_path / "audit.sqlite3") as opened:
        yield opened
