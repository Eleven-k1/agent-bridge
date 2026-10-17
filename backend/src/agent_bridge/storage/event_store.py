"""正式事件的原子持久化入口。

三条铁律在这里落地：

1. 序号分配与事件写入在同一个 `BEGIN IMMEDIATE` 事务内完成。不允许"先取号、
   再保存"，否则崩溃会留下永久缺口。
2. 三类作用域各自独立计数，互不替代。
3. 先持久化、后发布。事件落盘时进入待发布队列，M6 只能发布队列里的条目。

去重键相同但摘要不同是内容冲突，记录为事实并拒绝，绝不静默取首条或覆盖。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from .database import open_database
from .errors import DedupConflict, SequenceGapDetected

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scope_sequence (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    PRIMARY KEY (scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS formal_event (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    event_deduplication_key TEXT NOT NULL,
    candidate_event_digest TEXT NOT NULL,
    digest_algorithm_version TEXT NOT NULL,
    causation_id TEXT,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    workspace_identity TEXT,
    occurred_at TEXT NOT NULL,
    persisted_at TEXT NOT NULL,
    integrity_ref TEXT,
    published_at TEXT,
    PRIMARY KEY (scope_type, scope_id, sequence),
    UNIQUE (scope_type, scope_id, event_deduplication_key)
);
CREATE TABLE IF NOT EXISTS dedup_conflict (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    event_deduplication_key TEXT NOT NULL,
    existing_digest TEXT NOT NULL,
    rejected_digest TEXT NOT NULL,
    detected_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class PersistedEvent:
    """一条已落盘的正式事件。`duplicate` 为真表示这是同一事实的重复提交。"""

    scope_type: str
    scope_id: str
    sequence: int
    event_id: str
    event_type: str
    operation_id: str
    payload: dict[str, Any]
    duplicate: bool = False
    event_deduplication_key: str = ""
    candidate_event_digest: str = ""
    digest_algorithm_version: str = ""
    causation_id: str | None = None
    actor: str = ""
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None
    occurred_at: str = ""
    integrity_ref: str | None = None


class EventStore:
    """M5 的正式事件持久化权威。"""

    def __init__(self, path: Path | str) -> None:
        self._connection = open_database(path, _SCHEMA)
        try:
            self._connection.execute("ALTER TABLE formal_event ADD COLUMN integrity_ref TEXT")
        except Exception as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def __enter__(self) -> EventStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def append(self, candidate: Any) -> PersistedEvent:
        """原子追加一条候选事件，返回其作用域内序号。

        只读取 M0 候选事件的公共字段，不复制 M0 的类型定义。
        """
        scope_type = _scope_type(candidate)
        scope_id = candidate.scope_id
        self._require_scope_identity(candidate, scope_type)
        dedup_key = candidate.event_deduplication_key
        digest = candidate.candidate_event_digest
        now = datetime.now(UTC).isoformat()

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT * FROM formal_event WHERE scope_type = ? AND scope_id = ?"
                " AND event_deduplication_key = ?",
                (scope_type, scope_id, dedup_key),
            ).fetchone()
            if existing is not None:
                if existing["candidate_event_digest"] != digest:
                    self._connection.execute(
                        "INSERT INTO dedup_conflict (scope_type, scope_id,"
                        " event_deduplication_key, existing_digest, rejected_digest, detected_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (scope_type, scope_id, dedup_key, existing["candidate_event_digest"],
                         digest, now),
                    )
                    self._connection.execute("COMMIT")
                    raise DedupConflict(
                        f"deduplication key {dedup_key} already stores a different event digest"
                    )
                self._connection.execute("COMMIT")
                return self._from_row(existing, duplicate=True)

            sequence = self._reserve_sequence(scope_type, scope_id)
            self._connection.execute(
                "INSERT INTO formal_event (scope_type, scope_id, sequence, event_id, operation_id,"
                " event_deduplication_key, candidate_event_digest, digest_algorithm_version,"
                " causation_id, actor, event_type, payload, task_id, run_id, workspace_identity,"
                " occurred_at, persisted_at, integrity_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?)",
                (
                    scope_type,
                    scope_id,
                    sequence,
                    candidate.event_id,
                    candidate.operation_id,
                    dedup_key,
                    digest,
                    candidate.digest_algorithm_version,
                    getattr(candidate, "causation_id", None),
                    candidate.actor,
                    candidate.event_type,
                    json.dumps(dict(candidate.payload), ensure_ascii=False, sort_keys=True),
                    getattr(candidate, "task_id", None),
                    getattr(candidate, "run_id", None),
                    getattr(candidate, "workspace_identity", None),
                    _as_text(candidate.occurred_at),
                    now,
                    getattr(candidate, "integrity_ref", None),
                ),
            )
            self._connection.execute("COMMIT")
        except DedupConflict:
            raise
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return PersistedEvent(
            scope_type=scope_type,
            scope_id=scope_id,
            sequence=sequence,
            event_id=candidate.event_id,
            event_type=candidate.event_type,
            operation_id=candidate.operation_id,
            payload=dict(candidate.payload),
            event_deduplication_key=dedup_key,
            candidate_event_digest=digest,
            digest_algorithm_version=candidate.digest_algorithm_version,
            causation_id=getattr(candidate, "causation_id", None),
            actor=candidate.actor,
            task_id=getattr(candidate, "task_id", None),
            run_id=getattr(candidate, "run_id", None),
            workspace_identity=getattr(candidate, "workspace_identity", None),
            occurred_at=_as_text(candidate.occurred_at),
            integrity_ref=getattr(candidate, "integrity_ref", None),
        )

    def replay(
        self,
        *,
        scope_type: str,
        scope_id: str,
        after_sequence: int = 0,
    ) -> list[PersistedEvent]:
        """按序号重放。发现缺口时报告，绝不静默跳过。"""
        rows = self._connection.execute(
            "SELECT * FROM formal_event WHERE scope_type = ? AND scope_id = ? AND sequence > ?"
            " ORDER BY sequence",
            (scope_type, scope_id, after_sequence),
        ).fetchall()
        expected = after_sequence + 1
        for row in rows:
            if row["sequence"] != expected:
                raise SequenceGapDetected(
                    f"{scope_type}/{scope_id} is missing sequence {expected};"
                    f" next stored sequence is {row['sequence']}"
                )
            expected += 1
        self._require_no_trailing_gap(scope_type, scope_id, expected - 1)
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: Any, *, duplicate: bool = False) -> PersistedEvent:
        """从 SQLite 行恢复完整 M0 事件元数据，而非只恢复业务载荷。"""
        return PersistedEvent(
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            operation_id=row["operation_id"],
            payload=json.loads(row["payload"]),
            duplicate=duplicate,
            event_deduplication_key=row["event_deduplication_key"],
            candidate_event_digest=row["candidate_event_digest"],
            digest_algorithm_version=row["digest_algorithm_version"],
            causation_id=row["causation_id"],
            actor=row["actor"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            workspace_identity=row["workspace_identity"],
            occurred_at=row["occurred_at"],
            integrity_ref=row["integrity_ref"],
        )

    def pending_publications(self, *, scope_type: str, scope_id: str) -> list[int]:
        """M6 可以发布的序号。未落盘的事件不会出现在这里。"""
        rows = self._connection.execute(
            "SELECT sequence FROM formal_event WHERE scope_type = ? AND scope_id = ?"
            " AND published_at IS NULL ORDER BY sequence",
            (scope_type, scope_id),
        ).fetchall()
        return [row["sequence"] for row in rows]

    def scopes(self, *, scope_type: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT scope_id FROM formal_event WHERE scope_type = ? ORDER BY scope_id",
            (scope_type,),
        ).fetchall()
        return [row["scope_id"] for row in rows]

    def pending_scopes(self) -> list[tuple[str, str]]:
        rows = self._connection.execute(
            "SELECT DISTINCT scope_type, scope_id FROM formal_event"
            " WHERE published_at IS NULL ORDER BY scope_type, scope_id"
        ).fetchall()
        return [(row["scope_type"], row["scope_id"]) for row in rows]

    def mark_published(self, *, scope_type: str, scope_id: str, sequence: int) -> None:
        cursor = self._connection.execute(
            "UPDATE formal_event SET published_at = ? WHERE scope_type = ? AND scope_id = ?"
            " AND sequence = ?",
            (datetime.now(UTC).isoformat(), scope_type, scope_id, sequence),
        )
        if cursor.rowcount != 1:
            raise LookupError(
                f"cannot publish {scope_type}/{scope_id} sequence {sequence}: not persisted"
            )

    def dedup_conflicts(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM dedup_conflict ORDER BY conflict_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def simulate_storage_loss(self, *, scope_type: str, scope_id: str, sequence: int) -> None:
        """故障注入入口：制造一个存储缺口，用于验证缺口会被报告。

        只用于测试损坏检测路径。它不会改动序号计数器，因此模拟的是"行丢失"而
        不是"从未写入"。
        """
        self._connection.execute(
            "DELETE FROM formal_event WHERE scope_type = ? AND scope_id = ? AND sequence = ?",
            (scope_type, scope_id, sequence),
        )

    def _reserve_sequence(self, scope_type: str, scope_id: str) -> int:
        row = self._connection.execute(
            "SELECT last_sequence FROM scope_sequence WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        ).fetchone()
        nxt = (row["last_sequence"] if row else 0) + 1
        self._connection.execute(
            "INSERT INTO scope_sequence (scope_type, scope_id, last_sequence) VALUES (?, ?, ?)"
            " ON CONFLICT (scope_type, scope_id) DO UPDATE SET last_sequence = ?",
            (scope_type, scope_id, nxt, nxt),
        )
        return nxt

    def _require_no_trailing_gap(self, scope_type: str, scope_id: str, last_seen: int) -> None:
        row = self._connection.execute(
            "SELECT last_sequence FROM scope_sequence WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        ).fetchone()
        if row is not None and last_seen < row["last_sequence"]:
            raise SequenceGapDetected(
                f"{scope_type}/{scope_id} allocated sequence {row['last_sequence']}"
                f" but replay ended at {last_seen}"
            )

    @staticmethod
    def _require_scope_identity(candidate: Any, scope_type: str) -> None:
        if scope_type == "TASK":
            if not getattr(candidate, "task_id", None):
                raise ValueError("TASK event requires task_id")
            if not getattr(candidate, "run_id", None):
                raise ValueError("TASK event requires run_id")
        elif scope_type == "WORKSPACE":
            if not getattr(candidate, "workspace_identity", None):
                raise ValueError("WORKSPACE event requires workspace_identity")
        elif scope_type != "LOCAL_CONFIG":
            raise ValueError(f"unsupported scope_type: {scope_type}")


def _scope_type(carrier: Any) -> str:
    value = carrier.scope_type
    return value.value if hasattr(value, "value") else str(value)


def _as_text(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
