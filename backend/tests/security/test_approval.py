"""M3 审批票据测试：精确动作绑定、单次消费、过期、撤销、作用域校验。"""

from datetime import UTC, datetime, timedelta

import pytest

from agent_bridge.security.approval import ApprovalStore, TicketState
from agent_bridge.security.errors import ApprovalDenied
from agent_bridge.security.scope import ScopeRef

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
TASK_SCOPE = ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1", run_id="run-1")


@pytest.fixture
def store(tmp_path):
    with ApprovalStore(tmp_path / "approvals.sqlite3") as opened:
        yield opened


def grant(store: ApprovalStore, **overrides):
    data = {
        "scope": TASK_SCOPE,
        "target_operation_id": "operation-1",
        "canonical_action_digest": "action-digest-1",
        "granted_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    data.update(overrides)
    return store.grant(**data)


def test_granted_ticket_consumes_exactly_once(store: ApprovalStore) -> None:
    ticket = grant(store)
    store.consume(
        ticket_id=ticket.ticket_id,
        scope=TASK_SCOPE,
        target_operation_id="operation-1",
        canonical_action_digest="action-digest-1",
        now=NOW,
    )
    with pytest.raises(ApprovalDenied, match="consumed"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_ticket_does_not_cover_a_different_operation(store: ApprovalStore) -> None:
    ticket = grant(store)
    with pytest.raises(ApprovalDenied, match="operation"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-2",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_ticket_does_not_cover_a_changed_action(store: ApprovalStore) -> None:
    ticket = grant(store)
    with pytest.raises(ApprovalDenied, match="digest"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-tampered",
            now=NOW,
        )


def test_ticket_does_not_cover_another_task(store: ApprovalStore) -> None:
    ticket = grant(store)
    other = ScopeRef(scope_type="TASK", scope_id="task-2", task_id="task-2", run_id="run-1")
    with pytest.raises(ApprovalDenied, match="scope"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=other,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_ticket_does_not_survive_a_new_run(store: ApprovalStore) -> None:
    ticket = grant(store)
    later_run = ScopeRef(scope_type="TASK", scope_id="task-1", task_id="task-1", run_id="run-2")
    with pytest.raises(ApprovalDenied, match="run"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=later_run,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_expired_ticket_is_denied(store: ApprovalStore) -> None:
    ticket = grant(store)
    with pytest.raises(ApprovalDenied, match="expired"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW + timedelta(minutes=11),
        )


def test_revoked_ticket_is_denied(store: ApprovalStore) -> None:
    ticket = grant(store)
    store.revoke(ticket.ticket_id, reason="user revoked")
    with pytest.raises(ApprovalDenied, match="revoked"):
        store.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_denied_request_produces_no_ticket(store: ApprovalStore) -> None:
    record = store.deny(
        scope=TASK_SCOPE,
        target_operation_id="operation-1",
        canonical_action_digest="action-digest-1",
        decided_at=NOW,
        reason="user rejected",
    )
    assert record.state is TicketState.DENIED
    assert store.consumable(scope=TASK_SCOPE) == []


def test_unknown_ticket_is_denied(store: ApprovalStore) -> None:
    with pytest.raises(ApprovalDenied, match="unknown"):
        store.consume(
            ticket_id="no-such-ticket",
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_restart_does_not_resurrect_a_consumed_ticket(tmp_path) -> None:
    path = tmp_path / "approvals.sqlite3"
    with ApprovalStore(path) as first:
        ticket = grant(first)
        first.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )
    with ApprovalStore(path) as second, pytest.raises(ApprovalDenied, match="consumed"):
        second.consume(
            ticket_id=ticket.ticket_id,
            scope=TASK_SCOPE,
            target_operation_id="operation-1",
            canonical_action_digest="action-digest-1",
            now=NOW,
        )


def test_terminated_invariant_requires_no_consumable_ticket(store: ApprovalStore) -> None:
    ticket = grant(store)
    assert store.consumable(scope=TASK_SCOPE) == [ticket.ticket_id]
    store.invalidate_all(scope=TASK_SCOPE, reason="task terminated")
    assert store.consumable(scope=TASK_SCOPE) == []
