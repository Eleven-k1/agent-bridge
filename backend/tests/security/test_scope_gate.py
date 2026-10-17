"""M3 作用域门禁测试：无 Task 的项目副作用必须拒绝。"""

import pytest

from agent_bridge.security.errors import ScopeDenied
from agent_bridge.security.scope import Capability, ScopeGate, ScopeRef

TASK = ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1", run_id="run-1")
WORKSPACE = ScopeRef(scope_type="WORKSPACE", scope_id="ws-1", workspace_identity="ws-1")
LOCAL_CONFIG = ScopeRef(scope_type="LOCAL_CONFIG", scope_id="local-config")

PROJECT_SIDE_EFFECTS = [
    Capability.PROJECT_FILE_WRITE,
    Capability.SHELL_EXECUTE,
    Capability.GIT_WRITE,
    Capability.SNAPSHOT_RESTORE,
    Capability.OUTSIDE_PROJECT_ACCESS,
    Capability.SENSITIVE_SEND,
    Capability.WRITE_LEASE,
]


@pytest.fixture
def gate():
    return ScopeGate()


@pytest.mark.parametrize("capability", PROJECT_SIDE_EFFECTS)
def test_task_scope_may_request_project_capabilities(gate: ScopeGate, capability) -> None:
    assert gate.check(scope=TASK, capability=capability) is True


@pytest.mark.parametrize("capability", PROJECT_SIDE_EFFECTS)
def test_workspace_scope_is_denied_project_side_effects(gate: ScopeGate, capability) -> None:
    with pytest.raises(ScopeDenied):
        gate.check(scope=WORKSPACE, capability=capability)


@pytest.mark.parametrize("capability", PROJECT_SIDE_EFFECTS)
def test_local_config_scope_is_denied_project_side_effects(gate: ScopeGate, capability) -> None:
    with pytest.raises(ScopeDenied):
        gate.check(scope=LOCAL_CONFIG, capability=capability)


def test_workspace_scope_may_read(gate: ScopeGate) -> None:
    assert gate.check(scope=WORKSPACE, capability=Capability.WORKSPACE_READ) is True


def test_local_config_scope_may_manage_config(gate: ScopeGate) -> None:
    assert gate.check(scope=LOCAL_CONFIG, capability=Capability.LOCAL_CONFIG_WRITE) is True


def test_local_config_scope_cannot_read_workspace_facts(gate: ScopeGate) -> None:
    with pytest.raises(ScopeDenied):
        gate.check(scope=LOCAL_CONFIG, capability=Capability.WORKSPACE_READ)


def test_workspace_scope_cannot_write_local_config(gate: ScopeGate) -> None:
    with pytest.raises(ScopeDenied):
        gate.check(scope=WORKSPACE, capability=Capability.LOCAL_CONFIG_WRITE)


def test_task_scope_requires_task_and_run_ids() -> None:
    with pytest.raises(ValueError, match="run_id"):
        ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1")


def test_workspace_scope_requires_workspace_identity() -> None:
    with pytest.raises(ValueError, match="workspace_identity"):
        ScopeRef(scope_type="WORKSPACE", scope_id="ws-1")


def test_local_config_scope_does_not_require_workspace_identity() -> None:
    assert ScopeRef(scope_type="LOCAL_CONFIG", scope_id="local-config").workspace_identity is None


def test_unknown_scope_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="scope_type"):
        ScopeRef(scope_type="GLOBAL", scope_id="x")


def test_denial_message_names_the_capability_and_scope(gate: ScopeGate) -> None:
    with pytest.raises(ScopeDenied) as excinfo:
        gate.check(scope=LOCAL_CONFIG, capability=Capability.SHELL_EXECUTE)
    assert "SHELL_EXECUTE" in str(excinfo.value)
    assert "LOCAL_CONFIG" in str(excinfo.value)
