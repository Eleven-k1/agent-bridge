"""M4 工具执行门禁测试：先持久化意图、二次校验、无租约拒写、Fake Tool 边界。"""

import sys
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if find_spec("agent_bridge") is None and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_bridge.security.approval import ApprovalStore
from agent_bridge.security.errors import ApprovalDenied, LeaseDenied, PathDenied, ScopeDenied
from agent_bridge.security.lease import WriteLeaseRegistry
from agent_bridge.security.paths import PathPolicy
from agent_bridge.security.scope import ScopeRef
from agent_bridge.tools.errors import ToolRejected
from agent_bridge.tools.fake_tool import FakeTool
from agent_bridge.tools.gate import ToolGate, ToolRequest, ToolResultStatus

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".multi-agent").mkdir()
    return root


@pytest.fixture
def env(tmp_path, workspace):
    from agent_bridge.storage.event_store import EventStore

    workspace_identity = f"workspace:test:{workspace.name}"
    scope = ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1", run_id="run-1")
    with (
        EventStore(tmp_path / "audit.sqlite3") as store,
        ApprovalStore(tmp_path / "approvals.sqlite3") as approvals,
        WriteLeaseRegistry(tmp_path / "leases.sqlite3") as leases,
    ):
        lease = leases.acquire(
            workspace_identity=workspace_identity,
            scope=scope,
            holder="agent-a",
            granted_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
        )
        gate = ToolGate(
            event_store=store,
            approvals=approvals,
            leases=leases,
            path_policy=PathPolicy(workspace_root=workspace),
            tool=FakeTool(),
            workspace_identity=workspace_identity,
        )
        yield {
            "gate": gate,
            "store": store,
            "approvals": approvals,
            "leases": leases,
            "scope": scope,
            "lease": lease,
            "workspace": workspace,
            "workspace_identity": workspace_identity,
        }


def write_request(env, **overrides) -> ToolRequest:
    data = {
        "scope": env["scope"],
        "operation_id": "operation-1",
        "tool_name": "file_write",
        "target_path": env["workspace"] / "src" / "new.py",
        "arguments": {"content": "y = 2\n"},
        "lease_generation": env["lease"].generation,
        "holder": "agent-a",
    }
    data.update(overrides)
    return ToolRequest(**data)


def approve(env, request: ToolRequest) -> str:
    ticket = env["approvals"].grant(
        scope=request.scope,
        target_operation_id=request.operation_id,
        canonical_action_digest=env["gate"].canonical_action_digest(request),
        granted_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    return ticket.ticket_id


def test_approved_write_is_executed(env) -> None:
    request = write_request(env)
    result = env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)
    assert result.status is ToolResultStatus.COMPLETED


def test_write_without_a_ticket_is_rejected(env) -> None:
    with pytest.raises(ApprovalDenied):
        env["gate"].execute(write_request(env), ticket_id=None, now=NOW)


def test_write_without_a_valid_lease_is_rejected(env) -> None:
    request = write_request(env, lease_generation=99)
    with pytest.raises(LeaseDenied):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_write_by_another_holder_is_rejected(env) -> None:
    request = write_request(env, holder="agent-b")
    with pytest.raises(LeaseDenied, match="holder"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_write_outside_the_project_is_rejected(env) -> None:
    request = write_request(env, target_path=env["workspace"].parent / "outside.py")
    with pytest.raises(PathDenied, match="outside"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_write_into_the_audit_area_is_rejected(env) -> None:
    request = write_request(env, target_path=env["workspace"] / ".multi-agent" / "x.json")
    with pytest.raises(PathDenied, match="internal"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_ticket_for_a_different_action_is_rejected(env) -> None:
    approved = write_request(env)
    ticket_id = approve(env, approved)
    tampered = write_request(env, arguments={"content": "os.system('x')\n"})
    with pytest.raises(ApprovalDenied, match="digest"):
        env["gate"].execute(tampered, ticket_id=ticket_id, now=NOW)


def test_ticket_cannot_be_reused_for_a_second_write(env) -> None:
    request = write_request(env)
    ticket_id = approve(env, request)
    env["gate"].execute(request, ticket_id=ticket_id, now=NOW)
    with pytest.raises(ApprovalDenied, match="consumed"):
        env["gate"].execute(request, ticket_id=ticket_id, now=NOW)


def test_planned_intent_is_persisted_before_the_side_effect(env) -> None:
    request = write_request(env)
    env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)
    events = env["store"].replay(scope_type="TASK", scope_id="task-1")
    types = [item.event_type for item in events]
    assert types.index("TOOL_EXECUTION_PLANNED") < types.index("TOOL_EXECUTION_COMPLETED")


def test_rejected_request_records_no_planned_intent(env) -> None:
    with pytest.raises(ApprovalDenied):
        env["gate"].execute(write_request(env), ticket_id=None, now=NOW)
    events = env["store"].replay(scope_type="TASK", scope_id="task-1")
    assert [item.event_type for item in events] == []


def test_failed_execution_records_a_failure_result(env) -> None:
    request = write_request(env, tool_name="file_write", arguments={"content": None})
    result = env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)
    assert result.status is ToolResultStatus.FAILED
    types = [item.event_type for item in env["store"].replay(scope_type="TASK", scope_id="task-1")]
    assert types == ["TOOL_EXECUTION_PLANNED", "TOOL_EXECUTION_FAILED"]


def test_unknown_tool_is_rejected_before_planning(env) -> None:
    request = write_request(env, tool_name="rm_rf")
    with pytest.raises(ToolRejected, match="unknown tool"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)
    assert env["store"].replay(scope_type="TASK", scope_id="task-1") == []


def test_shell_is_not_available_in_phase_one(env) -> None:
    request = write_request(env, tool_name="shell_execute", arguments={"command": "dir"})
    with pytest.raises(ToolRejected, match="not implemented"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_git_write_is_not_available_in_phase_one(env) -> None:
    request = write_request(env, tool_name="git_commit", arguments={"message": "x"})
    with pytest.raises(ToolRejected, match="not implemented"):
        env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)


def test_read_does_not_require_a_lease(env) -> None:
    request = write_request(
        env,
        tool_name="file_read",
        target_path=env["workspace"] / "src" / "main.py",
        arguments={},
        lease_generation=None,
    )
    result = env["gate"].execute(request, ticket_id=None, now=NOW)
    assert result.status is ToolResultStatus.COMPLETED


def test_read_outside_the_project_is_still_rejected(env) -> None:
    request = write_request(
        env,
        tool_name="file_read",
        target_path=env["workspace"].parent / "outside.txt",
        arguments={},
        lease_generation=None,
    )
    with pytest.raises(PathDenied):
        env["gate"].execute(request, ticket_id=None, now=NOW)


def test_non_task_scope_cannot_write(env) -> None:
    scope = ScopeRef(scope_type="LOCAL_CONFIG", scope_id="local-config")
    request = write_request(env, scope=scope)
    with pytest.raises(ScopeDenied):
        env["gate"].execute(request, ticket_id=None, now=NOW)


def test_fake_tool_never_touches_the_real_filesystem(env) -> None:
    target = env["workspace"] / "src" / "new.py"
    request = write_request(env)
    env["gate"].execute(request, ticket_id=approve(env, request), now=NOW)
    assert not target.exists()
