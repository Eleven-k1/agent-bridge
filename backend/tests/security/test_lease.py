"""M3 WriteLease 测试：工作区级唯一写入者、代数单调、失效与终止不变量。"""

from datetime import UTC, datetime, timedelta

import pytest

from agent_bridge.security.errors import LeaseDenied
from agent_bridge.security.lease import WriteLeaseRegistry
from agent_bridge.security.scope import ScopeRef

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WORKSPACE = "workspace:volume-1:file-id-1"
TASK_SCOPE = ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1", run_id="run-1")


@pytest.fixture
def registry(tmp_path):
    with WriteLeaseRegistry(tmp_path / "leases.sqlite3") as opened:
        yield opened


def acquire(registry: WriteLeaseRegistry, holder: str = "agent-a", **overrides):
    data = {
        "workspace_identity": WORKSPACE,
        "scope": TASK_SCOPE,
        "holder": holder,
        "granted_at": NOW,
        "expires_at": NOW + timedelta(minutes=30),
    }
    data.update(overrides)
    return registry.acquire(**data)


def test_first_holder_gets_generation_one(registry: WriteLeaseRegistry) -> None:
    assert acquire(registry).generation == 1


def test_second_holder_is_denied_while_a_lease_is_valid(registry: WriteLeaseRegistry) -> None:
    acquire(registry)
    with pytest.raises(LeaseDenied, match="held"):
        acquire(registry, holder="agent-b")


def test_release_allows_a_new_holder_with_a_higher_generation(registry: WriteLeaseRegistry) -> None:
    first = acquire(registry)
    registry.release(workspace_identity=WORKSPACE, generation=first.generation)
    assert acquire(registry, holder="agent-b").generation == 2


def test_released_generation_can_no_longer_write(registry: WriteLeaseRegistry) -> None:
    first = acquire(registry)
    registry.release(workspace_identity=WORKSPACE, generation=first.generation)
    with pytest.raises(LeaseDenied, match="generation"):
        registry.require_valid(
            workspace_identity=WORKSPACE, generation=first.generation, holder="agent-a", now=NOW
        )


def test_expired_lease_cannot_write_but_can_be_superseded(registry: WriteLeaseRegistry) -> None:
    first = acquire(registry)
    later = NOW + timedelta(minutes=31)
    with pytest.raises(LeaseDenied, match="expired"):
        registry.require_valid(
            workspace_identity=WORKSPACE, generation=first.generation, holder="agent-a", now=later
        )
    assert acquire(registry, holder="agent-b", granted_at=later, expires_at=later).generation == 2


def test_holder_mismatch_is_denied(registry: WriteLeaseRegistry) -> None:
    lease = acquire(registry)
    with pytest.raises(LeaseDenied, match="holder"):
        registry.require_valid(
            workspace_identity=WORKSPACE, generation=lease.generation, holder="agent-b", now=NOW
        )


def test_leases_are_independent_per_workspace(registry: WriteLeaseRegistry) -> None:
    acquire(registry)
    other = acquire(registry, holder="agent-b", workspace_identity="workspace:volume-1:file-id-2")
    assert other.generation == 1


def test_lease_does_not_survive_process_restart_without_persistence(tmp_path) -> None:
    path = tmp_path / "leases.sqlite3"
    with WriteLeaseRegistry(path) as first:
        lease = acquire(first)
    with WriteLeaseRegistry(path) as second:
        with pytest.raises(LeaseDenied, match="held"):
            acquire(second, holder="agent-b")
        assert second.require_valid(
            workspace_identity=WORKSPACE, generation=lease.generation, holder="agent-a", now=NOW
        )


def test_generation_never_reused_after_restart(tmp_path) -> None:
    path = tmp_path / "leases.sqlite3"
    with WriteLeaseRegistry(path) as first:
        lease = acquire(first)
        first.release(workspace_identity=WORKSPACE, generation=lease.generation)
    with WriteLeaseRegistry(path) as second:
        assert acquire(second, holder="agent-b").generation == 2


def test_no_lease_at_all_is_denied(registry: WriteLeaseRegistry) -> None:
    with pytest.raises(LeaseDenied, match="no lease"):
        registry.require_valid(
            workspace_identity=WORKSPACE, generation=1, holder="agent-a", now=NOW
        )


def test_invalidate_all_supports_the_terminated_invariant(registry: WriteLeaseRegistry) -> None:
    acquire(registry)
    assert registry.valid_leases(scope=TASK_SCOPE)
    registry.invalidate_all(scope=TASK_SCOPE, reason="task terminated")
    assert registry.valid_leases(scope=TASK_SCOPE) == []
