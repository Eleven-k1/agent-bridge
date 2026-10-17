"""M5 事件持久化测试：原子追加、按作用域序号、先持久化后发布、缺口报告。"""

import pytest

from agent_bridge.storage.errors import DedupConflict, SequenceGapDetected
from agent_bridge.storage.event_store import EventStore

from .conftest import FakeCandidateEvent, task_event


def workspace_event(seq: int) -> FakeCandidateEvent:
    return FakeCandidateEvent(
        scope_type="WORKSPACE",
        scope_id="workspace-1",
        event_id=f"ws-event-{seq}",
        operation_id=f"ws-operation-{seq}",
        event_deduplication_key=f"ws-dedup-{seq}",
        candidate_event_digest=f"ws-digest-{seq}",
        workspace_identity="workspace-1",
    )


def local_config_event(seq: int) -> FakeCandidateEvent:
    return FakeCandidateEvent(
        scope_type="LOCAL_CONFIG",
        scope_id="local-config",
        event_id=f"lc-event-{seq}",
        operation_id=f"lc-operation-{seq}",
        event_deduplication_key=f"lc-dedup-{seq}",
        candidate_event_digest=f"lc-digest-{seq}",
    )


def test_sequences_start_at_one_per_scope(store: EventStore) -> None:
    assert store.append(task_event(1)).sequence == 1
    assert store.append(workspace_event(1)).sequence == 1
    assert store.append(local_config_event(1)).sequence == 1


def test_sequence_is_contiguous_within_a_scope(store: EventStore) -> None:
    sequences = [store.append(task_event(n)).sequence for n in range(1, 4)]
    assert sequences == [1, 2, 3]


def test_scopes_do_not_share_a_sequence(store: EventStore) -> None:
    store.append(task_event(1))
    store.append(task_event(2))
    assert store.append(workspace_event(1)).sequence == 1


def test_same_dedup_key_and_digest_is_idempotent(store: EventStore) -> None:
    first = store.append(task_event(1))
    second = store.append(task_event(1))
    assert second.sequence == first.sequence
    assert second.duplicate is True
    assert len(store.replay(scope_type="TASK", scope_id="task-1")) == 1


def test_same_dedup_key_different_digest_raises_and_records_conflict(store: EventStore) -> None:
    store.append(task_event(1))
    with pytest.raises(DedupConflict):
        store.append(task_event(1, candidate_event_digest="other-digest"))
    assert store.dedup_conflicts()
    assert len(store.replay(scope_type="TASK", scope_id="task-1")) == 1


def test_replay_returns_events_in_sequence_order(store: EventStore) -> None:
    for n in range(1, 4):
        store.append(task_event(n))
    replayed = store.replay(scope_type="TASK", scope_id="task-1")
    assert [item.sequence for item in replayed] == [1, 2, 3]


def test_replay_after_sequence_only_returns_later_events(store: EventStore) -> None:
    for n in range(1, 4):
        store.append(task_event(n))
    replayed = store.replay(scope_type="TASK", scope_id="task-1", after_sequence=2)
    assert [item.sequence for item in replayed] == [3]


def test_replay_reports_gap_instead_of_silently_skipping(store: EventStore) -> None:
    for n in range(1, 4):
        store.append(task_event(n))
    store.simulate_storage_loss(scope_type="TASK", scope_id="task-1", sequence=2)
    with pytest.raises(SequenceGapDetected):
        store.replay(scope_type="TASK", scope_id="task-1")


def test_publication_requires_a_persisted_event(store: EventStore) -> None:
    record = store.append(task_event(1))
    assert store.pending_publications(scope_type="TASK", scope_id="task-1") == [record.sequence]
    store.mark_published(scope_type="TASK", scope_id="task-1", sequence=record.sequence)
    assert store.pending_publications(scope_type="TASK", scope_id="task-1") == []


def test_marking_an_unpersisted_sequence_as_published_is_rejected(store: EventStore) -> None:
    with pytest.raises(LookupError):
        store.mark_published(scope_type="TASK", scope_id="task-1", sequence=1)


def test_task_event_without_run_id_is_rejected(store: EventStore) -> None:
    with pytest.raises(ValueError, match="run_id"):
        store.append(task_event(1, run_id=None))


def test_workspace_event_without_identity_is_rejected(store: EventStore) -> None:
    event = workspace_event(1)
    event.workspace_identity = None
    with pytest.raises(ValueError, match="workspace_identity"):
        store.append(event)


def test_local_config_event_does_not_require_workspace_identity(store: EventStore) -> None:
    assert store.append(local_config_event(1)).sequence == 1


def test_events_persist_across_reopen(tmp_path) -> None:
    path = tmp_path / "audit.sqlite3"
    with EventStore(path) as first:
        first.append(task_event(1))
    with EventStore(path) as second:
        assert second.append(task_event(2)).sequence == 2
