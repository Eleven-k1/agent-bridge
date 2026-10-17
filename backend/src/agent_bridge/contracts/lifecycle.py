"""M0 v0.5.1 task state dimensions and terminal invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class ContractStateError(ValueError):
    """A multidimensional task state contradicts the public contract."""


class TaskPhase(str, Enum):
    CREATED = "CREATED"
    EXECUTION = "EXECUTION"
    DISPOSITION = "DISPOSITION"
    RECOVERY = "RECOVERY"
    FINALIZED = "FINALIZED"


class Lifecycle(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    AWAITING_DISPOSITION = "AWAITING_DISPOSITION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    TERMINATED = "TERMINATED"


class FinalOutcome(str, Enum):
    OPEN = "OPEN"
    COMPLETED_NO_CHANGES = "COMPLETED_NO_CHANGES"
    COMPLETED_UNCOMMITTED = "COMPLETED_UNCOMMITTED"
    COMPLETED_COMMITTED = "COMPLETED_COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkspaceChangeState(str, Enum):
    NO_CHANGES = "NO_CHANGES"
    TASK_CHANGES_UNVERIFIED = "TASK_CHANGES_UNVERIFIED"
    TASK_CHANGES_VERIFIED = "TASK_CHANGES_VERIFIED"


class VerificationState(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    ENVIRONMENT_UNVERIFIED = "ENVIRONMENT_UNVERIFIED"


class BlockerStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class Blocker:
    blocker_id: str
    blocker_type: str
    status: BlockerStatus


@dataclass(frozen=True)
class TaskState:
    phase: TaskPhase = TaskPhase.CREATED
    lifecycle: Lifecycle = Lifecycle.CREATED
    final_outcome: FinalOutcome = FinalOutcome.OPEN
    workspace_change_state: WorkspaceChangeState = WorkspaceChangeState.NO_CHANGES
    verification_state: VerificationState = VerificationState.NOT_RUN
    blockers: tuple[Blocker, ...] = ()
    has_active_write_lease: bool = False
    has_consumable_approval: bool = False
    has_unresolved_operation: bool = False
    resumable_phase: TaskPhase | None = None

    @property
    def open_blocker_types(self) -> set[str]:
        return {blocker.blocker_type for blocker in self.blockers if blocker.status is BlockerStatus.OPEN}

    @property
    def is_verification_passed(self) -> bool:
        return self.verification_state is VerificationState.PASSED

    def with_new_workspace_modification(self) -> "TaskState":
        return replace(
            self,
            workspace_change_state=WorkspaceChangeState.TASK_CHANGES_UNVERIFIED,
            verification_state=VerificationState.NOT_RUN,
        )

    def assert_invariants(self) -> None:
        if self.lifecycle is Lifecycle.TERMINATED:
            if self.final_outcome is FinalOutcome.OPEN:
                raise ContractStateError("TERMINATED task cannot have OPEN final_outcome")
            if self.open_blocker_types:
                raise ContractStateError("TERMINATED task cannot have OPEN blocker")
            if self.has_active_write_lease:
                raise ContractStateError("TERMINATED task cannot have active WriteLease")
            if self.has_consumable_approval:
                raise ContractStateError("TERMINATED task cannot have consumable ApprovalTicket")
            if self.has_unresolved_operation:
                raise ContractStateError("TERMINATED task cannot have unresolved operation")
        if self.lifecycle is Lifecycle.AWAITING_DISPOSITION:
            if self.final_outcome is not FinalOutcome.OPEN:
                raise ContractStateError("AWAITING_DISPOSITION must retain OPEN final_outcome")
            if "STOP_DISPOSITION" not in self.open_blocker_types:
                raise ContractStateError("AWAITING_DISPOSITION requires STOP_DISPOSITION blocker")
        if self.lifecycle is Lifecycle.RECOVERY_REQUIRED and "RECOVERY_DECISION" not in self.open_blocker_types:
            raise ContractStateError("RECOVERY_REQUIRED requires RECOVERY_DECISION blocker")
