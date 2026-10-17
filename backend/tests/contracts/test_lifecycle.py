from __future__ import annotations

import pytest

from agent_bridge.contracts.lifecycle import (
    Blocker,
    BlockerStatus,
    ContractStateError,
    FinalOutcome,
    Lifecycle,
    TaskPhase,
    TaskState,
    VerificationState,
    WorkspaceChangeState,
)


def test_new_modification_invalidates_prior_verification() -> None:
    state = TaskState(
        phase=TaskPhase.EXECUTION,
        lifecycle=Lifecycle.ACTIVE,
        verification_state=VerificationState.PASSED,
        workspace_change_state=WorkspaceChangeState.TASK_CHANGES_VERIFIED,
    )

    changed = state.with_new_workspace_modification()

    assert changed.workspace_change_state is WorkspaceChangeState.TASK_CHANGES_UNVERIFIED
    assert changed.verification_state is VerificationState.NOT_RUN


def test_terminated_state_rejects_open_blocker_and_active_lease() -> None:
    state = TaskState(
        phase=TaskPhase.FINALIZED,
        lifecycle=Lifecycle.TERMINATED,
        final_outcome=FinalOutcome.CANCELLED,
        blockers=(Blocker("blocker-1", "TOOL_APPROVAL", BlockerStatus.OPEN),),
        has_active_write_lease=True,
    )

    with pytest.raises(ContractStateError, match="OPEN blocker"):
        state.assert_invariants()


def test_awaiting_disposition_remains_open() -> None:
    state = TaskState(
        phase=TaskPhase.DISPOSITION,
        lifecycle=Lifecycle.AWAITING_DISPOSITION,
        final_outcome=FinalOutcome.OPEN,
        blockers=(Blocker("blocker-1", "STOP_DISPOSITION", BlockerStatus.OPEN),),
    )

    state.assert_invariants()


def test_environment_unverified_is_not_passed() -> None:
    state = TaskState(
        phase=TaskPhase.EXECUTION,
        lifecycle=Lifecycle.ACTIVE,
        verification_state=VerificationState.ENVIRONMENT_UNVERIFIED,
    )

    assert not state.is_verification_passed
