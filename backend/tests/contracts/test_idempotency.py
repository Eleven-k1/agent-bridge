from __future__ import annotations

import pytest

from agent_bridge.contracts.envelopes import IdempotencyConflict, InMemoryIdempotencyRegistry


def test_command_duplicate_with_same_digest_returns_original_result() -> None:
    registry = InMemoryIdempotencyRegistry()
    first = registry.record_command("command-key", "digest-a", {"accepted": True})
    repeated = registry.record_command("command-key", "digest-a", {"accepted": False})

    assert first == {"accepted": True}
    assert repeated == first


@pytest.mark.parametrize(
    ("record", "identity"),
    [
        ("record_command", "command-key"),
        ("record_operation", "operation-1"),
        ("record_event", "operation-1:completed"),
    ],
)
def test_idempotency_identity_rejects_digest_conflict(record: str, identity: str) -> None:
    registry = InMemoryIdempotencyRegistry()
    method = getattr(registry, record)
    method(identity, "digest-a", "first")

    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_CONFLICT"):
        method(identity, "digest-b", "second")


def test_causation_id_is_not_used_as_an_event_deduplication_key() -> None:
    registry = InMemoryIdempotencyRegistry()
    registry.record_event("operation-1:started", "digest-started", "started")
    completed = registry.record_event(
        "operation-1:completed", "digest-completed", "completed"
    )

    assert completed == "completed"
