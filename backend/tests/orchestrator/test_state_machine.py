from __future__ import annotations

from dataclasses import replace

import pytest

from agent_bridge.contracts.envelopes import CommandEnvelope, ScopeType, canonical_digest
from agent_bridge.contracts.lifecycle import FinalOutcome, Lifecycle, TaskPhase
from agent_bridge.orchestrator.core import Orchestrator, TransitionError


class RecordingEventStore:
    def __init__(self) -> None:
        self.events = []

    def append_candidate(self, candidate):
        self.events.append(candidate)
        return candidate


def command(command_type: str, payload: dict | None = None) -> CommandEnvelope:
    payload = payload or {}
    return CommandEnvelope(
        protocol_version="0.5.1",
        scope_type=ScopeType.TASK,
        scope_id="task-1",
        command_id=f"command-{command_type}",
        command_idempotency_key=f"key-{command_type}",
        command_request_digest=canonical_digest(payload),
        issuer="user-1",
        command_type=command_type,
        payload=payload,
        issued_at="2026-08-30T00:00:00Z",
        task_id="task-1",
        run_id="run-1",
    )


def test_create_start_pause_and_resume_preserve_resumable_phase() -> None:
    store = RecordingEventStore()
    core = Orchestrator(store)
    core.handle(command("CREATE_TASK"))
    core.handle(command("START_TASK"))
    paused = core.handle(command("PAUSE_TASK"))
    resumed = core.handle(command("RESUME_TASK"))

    assert paused.lifecycle is Lifecycle.PAUSED
    assert paused.resumable_phase is TaskPhase.EXECUTION
    assert resumed.lifecycle is Lifecycle.ACTIVE
    assert resumed.phase is TaskPhase.EXECUTION
    assert [item.event_type for item in store.events] == [
        "TASK_CREATED",
        "TASK_STARTED",
        "TASK_PAUSED",
        "TASK_RESUMED",
    ]


def test_stop_then_retain_modifications_terminates_as_cancelled() -> None:
    core = Orchestrator(RecordingEventStore())
    core.handle(command("CREATE_TASK"))
    core.handle(command("START_TASK"))
    awaiting = core.handle(command("STOP_TASK"))
    terminated = core.handle(command("RETAIN_STOPPED_MODIFICATIONS"))

    assert awaiting.lifecycle is Lifecycle.AWAITING_DISPOSITION
    assert awaiting.final_outcome is FinalOutcome.OPEN
    assert terminated.lifecycle is Lifecycle.TERMINATED
    assert terminated.final_outcome is FinalOutcome.CANCELLED


def test_recovery_failure_cannot_terminate_and_creates_recovery_blocker() -> None:
    core = Orchestrator(RecordingEventStore())
    core.handle(command("CREATE_TASK"))
    core.handle(command("START_TASK"))
    recovered = core.handle(command("RECOVERY_RECONCILIATION_FAILED"))

    assert recovered.lifecycle is Lifecycle.RECOVERY_REQUIRED
    assert recovered.phase is TaskPhase.RECOVERY
    assert recovered.open_blocker_types == {"RECOVERY_DECISION"}

    with pytest.raises(TransitionError, match="recovery"):
        core.handle(command("TERMINATE_UNCOMMITTED"))


def test_restore_task_events_rebuilds_state_without_emitting_new_candidates() -> None:
    events = [
        {"task_event_sequence": 1, "event_type": "TASK_CREATED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 2, "event_type": "TASK_STARTED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 3, "event_type": "MODEL_OPERATION_PLANNED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 4, "event_type": "MODEL_OPERATION_STARTED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 5, "event_type": "MODEL_CONTENT_CHUNK_RECORDED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 6, "event_type": "MODEL_OPERATION_COMPLETED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 7, "event_type": "MODEL_OPERATION_FAILED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 8, "event_type": "MODEL_OPERATION_CANCEL_REQUESTED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 9, "event_type": "MODEL_OPERATION_CANCELLED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 10, "event_type": "TOOL_EXECUTION_PLANNED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 11, "event_type": "TOOL_EXECUTION_COMPLETED", "task_id": "task-1", "run_id": "run-1"},
        {"task_event_sequence": 12, "event_type": "TOOL_EXECUTION_FAILED", "task_id": "task-1", "run_id": "run-1"},
    ]
    store = RecordingEventStore()
    core = Orchestrator(store)

    restored = core.restore_task_events(events)

    assert restored == {("task-1", "run-1")}
    assert core.get_state("task-1", "run-1").lifecycle is Lifecycle.ACTIVE
    assert core.get_state("task-1", "run-1").phase is TaskPhase.EXECUTION
    assert store.events == []


@pytest.mark.parametrize("event_type", ["MODEL_UNKNOWN_EVENT", "TOOL_UNKNOWN_EVENT"])
def test_restore_task_events_rejects_unknown_model_or_tool_events(event_type: str) -> None:
    core = Orchestrator(RecordingEventStore())

    with pytest.raises(TransitionError, match=f"unsupported persisted event: {event_type}"):
        core.restore_task_events([
            {"task_event_sequence": 1, "event_type": "TASK_CREATED", "task_id": "task-1", "run_id": "run-1"},
            {"task_event_sequence": 2, "event_type": event_type, "task_id": "task-1", "run_id": "run-1"},
        ])


def test_restore_task_events_rejects_duplicate_task_created() -> None:
    core = Orchestrator(RecordingEventStore())

    with pytest.raises(TransitionError, match="task already exists"):
        core.restore_task_events([
            {"task_event_sequence": 1, "event_type": "TASK_CREATED", "task_id": "task-1", "run_id": "run-1"},
            {"task_event_sequence": 2, "event_type": "TASK_CREATED", "task_id": "task-1", "run_id": "run-1"},
        ])


def test_restore_task_events_rejects_missing_create_or_unknown_event() -> None:
    core = Orchestrator(RecordingEventStore())

    with pytest.raises(TransitionError, match="sequence 1"):
        core.restore_task_events([
            {"task_event_sequence": 2, "event_type": "TASK_STARTED", "task_id": "task-1", "run_id": "run-1"},
        ])

    with pytest.raises(TransitionError, match="unsupported persisted event"):
        core.restore_task_events([
            {"task_event_sequence": 1, "event_type": "TASK_CREATED", "task_id": "task-1", "run_id": "run-1"},
            {"task_event_sequence": 2, "event_type": "UNEXPECTED_EVENT", "task_id": "task-1", "run_id": "run-1"},
        ])
