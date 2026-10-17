"""命令持久化入口。

M6 转发的每条命令先在这里登记幂等键。相同键 + 相同摘要视为重复提交（断线重连
的正常情形），返回首次登记的 `command_id`，不产生第二次业务执行。相同键 + 不同
摘要是冲突：记录为事实并拒绝，绝不"后者覆盖前者"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any

from .database import open_database
from .errors import IdempotencyConflict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_entry (
    command_idempotency_key TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    command_request_digest TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    issuer TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_conflict (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_idempotency_key TEXT NOT NULL,
    existing_command_id TEXT NOT NULL,
    existing_digest TEXT NOT NULL,
    rejected_command_id TEXT NOT NULL,
    rejected_digest TEXT NOT NULL,
    detected_at TEXT NOT NULL
);
"""


class CommandRegistration(str, Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class RegistrationResult:
    status: CommandRegistration
    command_id: str
    command_idempotency_key: str


class CommandLog:
    """命令幂等登记的持久化权威。"""

    def __init__(self, path: Path | str) -> None:
        self._connection = open_database(path, _SCHEMA)

    def __enter__(self) -> CommandLog:
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

    def register(self, command: Any) -> RegistrationResult:
        """登记一条命令。只读取 M0 公共信封的字段，不复制其定义。"""
        key = command.command_idempotency_key
        digest = command.command_request_digest
        now = datetime.now(UTC).isoformat()

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT command_id, command_request_digest FROM command_entry"
                " WHERE command_idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["command_request_digest"] != digest:
                    self._connection.execute(
                        "INSERT INTO command_conflict (command_idempotency_key,"
                        " existing_command_id, existing_digest, rejected_command_id,"
                        " rejected_digest, detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            key,
                            existing["command_id"],
                            existing["command_request_digest"],
                            command.command_id,
                            digest,
                            now,
                        ),
                    )
                    self._connection.execute("COMMIT")
                    raise IdempotencyConflict(
                        f"idempotency key {key} was already used with a different request digest"
                    )
                self._connection.execute("COMMIT")
                return RegistrationResult(
                    status=CommandRegistration.DUPLICATE,
                    command_id=existing["command_id"],
                    command_idempotency_key=key,
                )

            self._connection.execute(
                "INSERT INTO command_entry (command_idempotency_key, command_id,"
                " command_request_digest, scope_type, scope_id, command_type, issuer,"
                " registered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    command.command_id,
                    digest,
                    _scope_type(command),
                    command.scope_id,
                    command.command_type,
                    command.issuer,
                    now,
                ),
            )
            self._connection.execute("COMMIT")
        except IdempotencyConflict:
            raise
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return RegistrationResult(
            status=CommandRegistration.ACCEPTED,
            command_id=command.command_id,
            command_idempotency_key=key,
        )

    def conflicts(self, *, idempotency_key: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM command_conflict WHERE command_idempotency_key = ? ORDER BY conflict_id",
            (idempotency_key,),
        ).fetchall()
        return [dict(row) for row in rows]


def _scope_type(carrier: Any) -> str:
    """兼容 M0 的 ScopeType 枚举与纯字符串。"""
    value = carrier.scope_type
    return value.value if hasattr(value, "value") else str(value)
