"""审批票据。

一张票据只对一个 `(scope, target_operation_id, canonical_action_digest)` 组合有效，
且只能消费一次。落盘是唯一权威：进程重启不得让已消费或已撤销的票据复活。

消费在单个 SQLite 事务里完成条件更新，避免 `remaining_uses` 被并发多减。
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType

from .errors import ApprovalDenied
from .scope import ScopeRef

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_ticket (
    ticket_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    target_operation_id TEXT NOT NULL,
    canonical_action_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_reason TEXT
);
CREATE INDEX IF NOT EXISTS approval_ticket_scope
    ON approval_ticket (scope_type, scope_id, state);
"""


class TicketState(str, Enum):
    GRANTED = "GRANTED"
    CONSUMED = "CONSUMED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    state: TicketState
    target_operation_id: str
    canonical_action_digest: str


class ApprovalStore:
    """审批票据的持久化权威。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)

    def __enter__(self) -> ApprovalStore:
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

    def grant(
        self,
        *,
        scope: ScopeRef,
        target_operation_id: str,
        canonical_action_digest: str,
        granted_at: datetime,
        expires_at: datetime,
    ) -> TicketRecord:
        """记录一次用户批准。票据只绑定这一个操作和这一个动作摘要。"""
        ticket_id = f"ticket-{uuid.uuid4()}"
        self._connection.execute(
            "INSERT INTO approval_ticket (ticket_id, scope_type, scope_id, task_id, run_id,"
            " target_operation_id, canonical_action_digest, state, granted_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                scope.scope_type,
                scope.scope_id,
                scope.task_id,
                scope.run_id,
                target_operation_id,
                canonical_action_digest,
                TicketState.GRANTED.value,
                granted_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        return TicketRecord(
            ticket_id=ticket_id,
            state=TicketState.GRANTED,
            target_operation_id=target_operation_id,
            canonical_action_digest=canonical_action_digest,
        )

    def deny(
        self,
        *,
        scope: ScopeRef,
        target_operation_id: str,
        canonical_action_digest: str,
        decided_at: datetime,
        reason: str,
    ) -> TicketRecord:
        """记录一次拒绝。拒绝也是事实，但不产生可消费的票据。"""
        ticket_id = f"denied-{uuid.uuid4()}"
        self._connection.execute(
            "INSERT INTO approval_ticket (ticket_id, scope_type, scope_id, task_id, run_id,"
            " target_operation_id, canonical_action_digest, state, granted_at, expires_at,"
            " resolved_at, resolution_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                scope.scope_type,
                scope.scope_id,
                scope.task_id,
                scope.run_id,
                target_operation_id,
                canonical_action_digest,
                TicketState.DENIED.value,
                decided_at.isoformat(),
                decided_at.isoformat(),
                decided_at.isoformat(),
                reason,
            ),
        )
        return TicketRecord(
            ticket_id=ticket_id,
            state=TicketState.DENIED,
            target_operation_id=target_operation_id,
            canonical_action_digest=canonical_action_digest,
        )

    def consume(
        self,
        *,
        ticket_id: str,
        scope: ScopeRef,
        target_operation_id: str,
        canonical_action_digest: str,
        now: datetime,
    ) -> TicketRecord:
        """落地副作用前的最后一道校验，并原子地消费票据。

        校验必须在这里而不是在申请时重做一遍：申请与落地之间目标可能被改写
        (TOCTOU)，所以摘要要在消费的同一事务里比对。
        """
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                "SELECT * FROM approval_ticket WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise ApprovalDenied(f"unknown approval ticket: {ticket_id}")
            self._reject_unusable_state(row)
            self._reject_mismatch(
                row,
                scope=scope,
                target_operation_id=target_operation_id,
                canonical_action_digest=canonical_action_digest,
            )
            if now > datetime.fromisoformat(row["expires_at"]):
                raise ApprovalDenied("approval ticket has expired")

            updated = self._connection.execute(
                "UPDATE approval_ticket SET state = ?, resolved_at = ?, resolution_reason = ?"
                " WHERE ticket_id = ? AND state = ?",
                (
                    TicketState.CONSUMED.value,
                    now.isoformat(),
                    "consumed for approved operation",
                    ticket_id,
                    TicketState.GRANTED.value,
                ),
            )
            if updated.rowcount != 1:
                raise ApprovalDenied("approval ticket was already consumed")
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return TicketRecord(
            ticket_id=ticket_id,
            state=TicketState.CONSUMED,
            target_operation_id=target_operation_id,
            canonical_action_digest=canonical_action_digest,
        )

    def revoke(self, ticket_id: str, *, reason: str) -> None:
        """用户撤销批准。已消费的票据不改状态，撤销对它没有意义。"""
        self._connection.execute(
            "UPDATE approval_ticket SET state = ?, resolution_reason = ? WHERE ticket_id = ?"
            " AND state = ?",
            (TicketState.REVOKED.value, reason, ticket_id, TicketState.GRANTED.value),
        )

    def invalidate_all(self, *, scope: ScopeRef, reason: str) -> int:
        """作用域收尾时批量失效，支撑 TERMINATED 的"无可消费票据"不变量。"""
        cursor = self._connection.execute(
            "UPDATE approval_ticket SET state = ?, resolution_reason = ?"
            " WHERE scope_type = ? AND scope_id = ? AND state = ?",
            (
                TicketState.INVALIDATED.value,
                reason,
                scope.scope_type,
                scope.scope_id,
                TicketState.GRANTED.value,
            ),
        )
        return cursor.rowcount

    def consumable(self, *, scope: ScopeRef) -> list[str]:
        rows = self._connection.execute(
            "SELECT ticket_id FROM approval_ticket WHERE scope_type = ? AND scope_id = ?"
            " AND state = ? ORDER BY granted_at",
            (scope.scope_type, scope.scope_id, TicketState.GRANTED.value),
        ).fetchall()
        return [row["ticket_id"] for row in rows]

    @staticmethod
    def _reject_unusable_state(row: sqlite3.Row) -> None:
        state = TicketState(row["state"])
        if state is TicketState.CONSUMED:
            raise ApprovalDenied("approval ticket was already consumed")
        if state is TicketState.REVOKED:
            raise ApprovalDenied("approval ticket was revoked")
        if state is TicketState.INVALIDATED:
            raise ApprovalDenied("approval ticket was invalidated")
        if state is TicketState.DENIED:
            raise ApprovalDenied("approval request was denied")

    @staticmethod
    def _reject_mismatch(
        row: sqlite3.Row,
        *,
        scope: ScopeRef,
        target_operation_id: str,
        canonical_action_digest: str,
    ) -> None:
        if row["scope_type"] != scope.scope_type or row["scope_id"] != scope.scope_id:
            raise ApprovalDenied("approval ticket belongs to another scope")
        if row["task_id"] != scope.task_id:
            raise ApprovalDenied("approval ticket belongs to another scope")
        # 新 Run 意味着一次重启或恢复，旧批准不得沿用。
        if row["run_id"] != scope.run_id:
            raise ApprovalDenied("approval ticket does not survive a new run")
        if row["target_operation_id"] != target_operation_id:
            raise ApprovalDenied("approval ticket targets another operation")
        if row["canonical_action_digest"] != canonical_action_digest:
            raise ApprovalDenied("approved action digest does not match this request")
