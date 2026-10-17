from __future__ import annotations

import pytest

from agent_bridge.contracts.envelopes import (
    CommandEnvelope,
    ContractValidationError,
    EventEnvelope,
    ModelStreamFrame,
    ScopeType,
    canonical_digest,
)


def make_command(scope_type: ScopeType, **overrides: object) -> CommandEnvelope:
    payload = {"action": "inspect"}
    data: dict[str, object] = {
        "protocol_version": "0.5.1",
        "scope_type": scope_type,
        "scope_id": "task-1" if scope_type is ScopeType.TASK else "workspace-1",
        "command_id": "command-1",
        "command_idempotency_key": "command-key-1",
        "command_request_digest": canonical_digest(payload),
        "issuer": "user-1",
        "command_type": "INSPECT",
        "payload": payload,
        "issued_at": "2026-08-30T00:00:00Z",
    }
    if scope_type is ScopeType.TASK:
        data.update(task_id="task-1", run_id="run-1")
    if scope_type is ScopeType.WORKSPACE:
        data.update(workspace_identity="workspace-1")
    data.update(overrides)
    return CommandEnvelope(**data)


def test_task_command_requires_task_and_run_ids() -> None:
    with pytest.raises(ContractValidationError, match="run_id"):
        make_command(ScopeType.TASK, run_id=None)


def test_workspace_command_requires_workspace_identity() -> None:
    with pytest.raises(ContractValidationError, match="workspace_identity"):
        make_command(ScopeType.WORKSPACE, workspace_identity=None)


def test_local_config_command_needs_no_workspace_identity() -> None:
    command = make_command(
        ScopeType.LOCAL_CONFIG,
        scope_id="local-config",
        workspace_identity=None,
    )

    assert command.scope_type is ScopeType.LOCAL_CONFIG
    assert command.workspace_identity is None


@pytest.mark.parametrize(
    ("scope_type", "sequence_field"),
    [
        (ScopeType.TASK, "task_event_sequence"),
        (ScopeType.WORKSPACE, "workspace_event_sequence"),
        (ScopeType.LOCAL_CONFIG, "local_config_event_sequence"),
    ],
)
def test_formal_event_has_one_scope_specific_sequence(
    scope_type: ScopeType, sequence_field: str
) -> None:
    payload = {"state": "recorded"}
    event = EventEnvelope(
        protocol_version="0.5.1",
        scope_type=scope_type,
        scope_id="task-1" if scope_type is ScopeType.TASK else "workspace-1",
        event_id="event-1",
        operation_id="operation-1",
        event_deduplication_key="operation-1:recorded",
        causation_id="command-1",
        actor="M5",
        event_type="FACT_RECORDED",
        payload=payload,
        candidate_event_digest=canonical_digest(payload),
        digest_algorithm_version="sha256-canonical-json-v1",
        occurred_at="2026-08-30T00:00:00Z",
        integrity_ref="integrity:event-1",
        task_id="task-1" if scope_type is ScopeType.TASK else None,
        run_id="run-1" if scope_type is ScopeType.TASK else None,
        workspace_identity="workspace-1" if scope_type is ScopeType.WORKSPACE else None,
        task_event_sequence=7 if scope_type is ScopeType.TASK else None,
        workspace_event_sequence=7 if scope_type is ScopeType.WORKSPACE else None,
        local_config_event_sequence=7 if scope_type is ScopeType.LOCAL_CONFIG else None,
    )

    encoded = event.to_dict()
    assert encoded[sequence_field] == 7
    assert sum(key.endswith("_event_sequence") for key in encoded) == 1


def test_model_stream_frame_is_transient_and_has_no_formal_event_sequence() -> None:
    frame = ModelStreamFrame(
        scope_type=ScopeType.TASK,
        task_id="task-1",
        run_id="run-1",
        operation_id="operation-1",
        model_fragment_sequence=3,
        frame_type="MODEL_DELTA_FRAME",
        content_delta_ref="content:operation-1:3",
        received_at="2026-08-30T00:00:00Z",
    )

    encoded = frame.to_dict()
    assert "event_id" not in encoded
    assert "task_event_sequence" not in encoded
    assert encoded["model_fragment_sequence"] == 3
