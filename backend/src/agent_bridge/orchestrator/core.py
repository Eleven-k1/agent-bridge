"""Minimal M1 task-state authority for the first implementation stage."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable
from agent_bridge.contracts.envelopes import CommandEnvelope, ScopeType
from agent_bridge.contracts.lifecycle import (
    Blocker,
    BlockerStatus,
    FinalOutcome,
    Lifecycle,
    TaskPhase,
    TaskState,
)
from .ports import CandidateEventPort


_IGNORED_TASK_EVENT_TYPES = frozenset(
    {
        "MODEL_OPERATION_PLANNED",
        "MODEL_OPERATION_STARTED",
        "MODEL_CONTENT_CHUNK_RECORDED",
        "MODEL_OPERATION_COMPLETED",
        "MODEL_OPERATION_FAILED",
        "MODEL_OPERATION_CANCEL_REQUESTED",
        "MODEL_OPERATION_CANCELLED",
        "TOOL_EXECUTION_PLANNED",
        "TOOL_EXECUTION_COMPLETED",
        "TOOL_EXECUTION_FAILED",
    }
)


class TransitionError(ValueError):
    """A task command cannot advance the current M1 state."""


@dataclass(frozen=True)
class CandidateFact:
    event_type: str
    task_id: str
    run_id: str
    causation_id: str


class Orchestrator:
    """The only authority that changes task-state dimensions in M1."""

    def __init__(self, event_port: CandidateEventPort) -> None:
        self._event_port = event_port
        self._states: dict[tuple[str, str], TaskState] = {}

    def handle(self, command: CommandEnvelope) -> TaskState:
        if command.scope_type is not ScopeType.TASK:
            raise TransitionError("M1 only accepts TASK commands")
        key = (command.task_id or "", command.run_id or "")
        current = self._states.get(key, TaskState())
        next_state, event_type = self._transition(current, command.command_type)
        next_state.assert_invariants()
        self._event_port.append_candidate(
            CandidateFact(
                event_type=event_type,
                task_id=key[0],
                run_id=key[1],
                causation_id=command.command_id,
            )
        )
        self._states[key] = next_state
        return next_state

    def get_state(self, task_id: str, run_id: str) -> TaskState:
        """Return the current state, using the contract's initial state when absent."""
        return self._states.get((task_id, run_id), TaskState())

    def restore_task_events(self, events: Iterable[Any]) -> set[tuple[str, str]]:
        """Rebuild M1 state from ordered persisted TASK events without emitting facts."""
        event_to_command = {
            "TASK_CREATED": "CREATE_TASK",
            "TASK_STARTED": "START_TASK",
            "TASK_PAUSED": "PAUSE_TASK",
            "TASK_RESUMED": "RESUME_TASK",
            "TASK_STOPPED_AWAITING_DISPOSITION": "STOP_TASK",
            "TASK_STOP_DISPOSITION_RESOLVED": "RETAIN_STOPPED_MODIFICATIONS",
            "RECOVERY_RECONCILIATION_FAILED": "RECOVERY_RECONCILIATION_FAILED",
            "TASK_COMPLETED_UNCOMMITTED": "TERMINATE_UNCOMMITTED",
        }
        restored: set[tuple[str, str]] = set()
        expected_sequence = 1
        for event in events:
            sequence = _event_value(event, "task_event_sequence")
            if sequence is None:
                sequence = _event_value(event, "sequence")
            if sequence != expected_sequence:
                raise TransitionError(
                    f"cannot restore task events: expected sequence {expected_sequence}, got {sequence}"
                )
            expected_sequence += 1
            event_type = _event_value(event, "event_type")
            task_id = _event_value(event, "task_id")
            run_id = _event_value(event, "run_id")
            if not task_id or not run_id:
                raise TransitionError("persisted TASK event requires task_id and run_id")
            if not isinstance(event_type, str):
                raise TransitionError("unsupported persisted event: missing event_type")
            key = (task_id, run_id)
            command_type = event_to_command.get(event_type)
            if command_type is None:
                if event_type in _IGNORED_TASK_EVENT_TYPES:
                    continue
                raise TransitionError(f"unsupported persisted event: {event_type}")
            if command_type == "CREATE_TASK" and key in restored:
                raise TransitionError("task already exists")
            if command_type != "CREATE_TASK" and key not in restored:
                raise TransitionError(
                    f"cannot restore task events: sequence {sequence} starts without TASK_CREATED"
                )
            current = self._states.get(key, TaskState())
            next_state, _ = self._transition(current, command_type)
            next_state.assert_invariants()
            self._states[key] = next_state
            restored.add(key)
        return restored

    def _transition(self, state: TaskState, command_type: str) -> tuple[TaskState, str]:
        if command_type == "CREATE_TASK":
            if state.lifecycle is not Lifecycle.CREATED or state.phase is not TaskPhase.CREATED:
                raise TransitionError("task already exists")
            return state, "TASK_CREATED"
        if command_type == "START_TASK":
            self._require(state, Lifecycle.CREATED)
            return replace(state, phase=TaskPhase.EXECUTION, lifecycle=Lifecycle.ACTIVE), "TASK_STARTED"
        if command_type == "PAUSE_TASK":
            self._require(state, Lifecycle.ACTIVE)
            return replace(state, lifecycle=Lifecycle.PAUSED, resumable_phase=state.phase), "TASK_PAUSED"
        if command_type == "RESUME_TASK":
            self._require(state, Lifecycle.PAUSED)
            if state.resumable_phase is None:
                raise TransitionError("paused task lacks resumable phase")
            return replace(state, lifecycle=Lifecycle.ACTIVE, phase=state.resumable_phase, resumable_phase=None), "TASK_RESUMED"
        if command_type == "STOP_TASK":
            if state.lifecycle not in {Lifecycle.ACTIVE, Lifecycle.PAUSED}:
                raise TransitionError("stop requires active or paused task")
            return (
                replace(
                    state,
                    phase=TaskPhase.DISPOSITION,
                    lifecycle=Lifecycle.AWAITING_DISPOSITION,
                    blockers=(Blocker("stop-disposition", "STOP_DISPOSITION", BlockerStatus.OPEN),),
                    has_active_write_lease=False,
                    has_consumable_approval=False,
                    has_unresolved_operation=False,
                ),
                "TASK_STOPPED_AWAITING_DISPOSITION",
            )
        if command_type in {"RETAIN_STOPPED_MODIFICATIONS", "RESTORE_STOPPED_MODIFICATIONS"}:
            self._require(state, Lifecycle.AWAITING_DISPOSITION)
            return (
                replace(
                    state,
                    phase=TaskPhase.FINALIZED,
                    lifecycle=Lifecycle.TERMINATED,
                    final_outcome=FinalOutcome.CANCELLED,
                    blockers=(),
                ),
                "TASK_STOP_DISPOSITION_RESOLVED",
            )
        if command_type == "RECOVERY_RECONCILIATION_FAILED":
            if state.lifecycle is Lifecycle.TERMINATED:
                raise TransitionError("terminated task cannot require recovery")
            return (
                replace(
                    state,
                    phase=TaskPhase.RECOVERY,
                    lifecycle=Lifecycle.RECOVERY_REQUIRED,
                    blockers=(Blocker("recovery-decision", "RECOVERY_DECISION", BlockerStatus.OPEN),),
                    has_active_write_lease=False,
                    has_consumable_approval=False,
                    has_unresolved_operation=False,
                ),
                "RECOVERY_RECONCILIATION_FAILED",
            )
        if command_type == "TERMINATE_UNCOMMITTED":
            if state.lifecycle is Lifecycle.RECOVERY_REQUIRED:
                raise TransitionError("recovery must be reconciled before termination")
            self._require(state, Lifecycle.ACTIVE)
            return (
                replace(
                    state,
                    phase=TaskPhase.FINALIZED,
                    lifecycle=Lifecycle.TERMINATED,
                    final_outcome=FinalOutcome.COMPLETED_UNCOMMITTED,
                    blockers=(),
                    has_active_write_lease=False,
                    has_consumable_approval=False,
                    has_unresolved_operation=False,
                ),
                "TASK_COMPLETED_UNCOMMITTED",
            )
        raise TransitionError(f"unsupported task command: {command_type}")

    @staticmethod
    def _require(state: TaskState, lifecycle: Lifecycle) -> None:
        if state.lifecycle is not lifecycle:
            raise TransitionError(f"expected lifecycle {lifecycle.value}, got {state.lifecycle.value}")


def _event_value(event: Any, field: str) -> Any:
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)
