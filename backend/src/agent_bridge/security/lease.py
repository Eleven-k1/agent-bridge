"""工作区级唯一写入者租约。

任一工作区在任一时刻最多有一个有效租约。`generation` 单调递增且永不复用：
换执行者后旧代数即失效，即使它的持有者仍在内存里以为自己有写权限。

落盘是唯一权威，内存缓存不参与判定，因此崩溃重启后租约状态不会凭空消失。
`generation` 由 UNIQUE 约束保证不重复，抢锁竞争由 BEGIN IMMEDIATE 串行化。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType

from .errors import LeaseDenied
from .scope import ScopeRef

_SCHEMA = """
CREATE TABLE IF NOT EXISTS write_lease (
    workspace_identity TEXT NOT NULL,
    generation INTEGER NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    holder TEXT NOT NULL,
    state TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    PRIMARY KEY (workspace_identity, generation)
);
CREATE INDEX IF NOT EXISTS write_lease_scope ON write_lease (scope_type, scope_id, state);
"""


class LeaseState(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class LeaseRecord:
    workspace_identity: str
    generation: int
    holder: str
    state: LeaseState


class WriteLeaseRegistry:
    """唯一写入者规则的持久化权威。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_SCHEMA)

    def __enter__(self) -> WriteLeaseRegistry:
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

    def acquire(
        self,
        *,
        workspace_identity: str,
        scope: ScopeRef,
        holder: str,
        granted_at: datetime,
        expires_at: datetime,
    ) -> LeaseRecord:
        """申请写权限。已有未过期的有效租约时直接拒绝，不排队、不抢占。"""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            active = self._active_row(workspace_identity)
            if active is not None and granted_at <= datetime.fromisoformat(active["expires_at"]):
                raise LeaseDenied(
                    f"write lease is already held by {active['holder']}"
                    f" at generation {active['generation']}"
                )
            if active is not None:
                # 过期租约不能继续写，但必须显式作废，否则代数会被两个持有者共享。
                self._connection.execute(
                    "UPDATE write_lease SET state = ?, released_at = ?, release_reason = ?"
                    " WHERE workspace_identity = ? AND generation = ?",
                    (
                        LeaseState.INVALIDATED.value,
                        granted_at.isoformat(),
                        "superseded after expiry",
                        workspace_identity,
                        active["generation"],
                    ),
                )
            generation = self._next_generation(workspace_identity)
            self._connection.execute(
                "INSERT INTO write_lease (workspace_identity, generation, scope_type, scope_id,"
                " task_id, run_id, holder, state, granted_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_identity,
                    generation,
                    scope.scope_type,
                    scope.scope_id,
                    scope.task_id,
                    scope.run_id,
                    holder,
                    LeaseState.ACTIVE.value,
                    granted_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return LeaseRecord(
            workspace_identity=workspace_identity,
            generation=generation,
            holder=holder,
            state=LeaseState.ACTIVE,
        )

    def require_valid(
        self,
        *,
        workspace_identity: str,
        generation: int,
        holder: str,
        now: datetime,
    ) -> LeaseRecord:
        """每次落盘前都要调用。只认落盘状态，不认调用方声明。"""
        row = self._connection.execute(
            "SELECT * FROM write_lease WHERE workspace_identity = ? AND generation = ?",
            (workspace_identity, generation),
        ).fetchone()
        if row is None:
            active = self._active_row(workspace_identity)
            if active is None:
                raise LeaseDenied(f"no lease exists for workspace {workspace_identity}")
            raise LeaseDenied(
                f"generation {generation} is not the current lease generation"
                f" {active['generation']}"
            )
        state = LeaseState(row["state"])
        if state is not LeaseState.ACTIVE:
            raise LeaseDenied(f"lease generation {generation} is {state.value.lower()}")
        if row["holder"] != holder:
            raise LeaseDenied("lease holder does not match the requester")
        if now > datetime.fromisoformat(row["expires_at"]):
            raise LeaseDenied(f"lease generation {generation} has expired")
        return LeaseRecord(
            workspace_identity=workspace_identity,
            generation=generation,
            holder=holder,
            state=state,
        )

    def release(
        self,
        *,
        workspace_identity: str,
        generation: int,
        reason: str = "released by holder",
    ) -> None:
        self._connection.execute(
            "UPDATE write_lease SET state = ?, release_reason = ? WHERE workspace_identity = ?"
            " AND generation = ? AND state = ?",
            (
                LeaseState.RELEASED.value,
                reason,
                workspace_identity,
                generation,
                LeaseState.ACTIVE.value,
            ),
        )

    def invalidate_all(self, *, scope: ScopeRef, reason: str) -> int:
        """作用域收尾时批量失效，支撑 TERMINATED 的"无有效租约"不变量。"""
        cursor = self._connection.execute(
            "UPDATE write_lease SET state = ?, release_reason = ? WHERE scope_type = ?"
            " AND scope_id = ? AND state = ?",
            (
                LeaseState.INVALIDATED.value,
                reason,
                scope.scope_type,
                scope.scope_id,
                LeaseState.ACTIVE.value,
            ),
        )
        return cursor.rowcount

    def valid_leases(self, *, scope: ScopeRef) -> list[LeaseRecord]:
        rows = self._connection.execute(
            "SELECT * FROM write_lease WHERE scope_type = ? AND scope_id = ? AND state = ?",
            (scope.scope_type, scope.scope_id, LeaseState.ACTIVE.value),
        ).fetchall()
        return [
            LeaseRecord(
                workspace_identity=row["workspace_identity"],
                generation=row["generation"],
                holder=row["holder"],
                state=LeaseState.ACTIVE,
            )
            for row in rows
        ]

    def _active_row(self, workspace_identity: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM write_lease WHERE workspace_identity = ? AND state = ?"
            " ORDER BY generation DESC LIMIT 1",
            (workspace_identity, LeaseState.ACTIVE.value),
        ).fetchone()

    def _next_generation(self, workspace_identity: str) -> int:
        row = self._connection.execute(
            "SELECT MAX(generation) AS current FROM write_lease WHERE workspace_identity = ?",
            (workspace_identity,),
        ).fetchone()
        return (row["current"] or 0) + 1
