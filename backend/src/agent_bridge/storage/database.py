"""SQLite 连接的共用打开方式。

`isolation_level=None` 关闭 sqlite3 的隐式事务管理，让本模块显式控制
`BEGIN IMMEDIATE`，序号分配和幂等判定才能在同一事务里完成。
`synchronous=FULL` 是有意的：审计事实不能因为断电而丢，性能不是这里的优先项。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_database(path: Path | str, schema: str) -> sqlite3.Connection:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(schema)
    return connection
