"""作用域引用与能力门禁。

M0 v0.5.1 只有三类作用域。项目副作用能力必须绑定 TASK；WORKSPACE 只读工作区
事实；LOCAL_CONFIG 只能操作本地配置，不得获得项目文件、Shell、Git 或 WriteLease。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import ScopeDenied

SCOPE_TYPES = frozenset({"TASK", "WORKSPACE", "LOCAL_CONFIG"})


class Capability(str, Enum):
    """需要作用域授权的能力。"""

    PROJECT_FILE_READ = "PROJECT_FILE_READ"
    PROJECT_FILE_WRITE = "PROJECT_FILE_WRITE"
    SHELL_EXECUTE = "SHELL_EXECUTE"
    GIT_READ = "GIT_READ"
    GIT_WRITE = "GIT_WRITE"
    SNAPSHOT_RESTORE = "SNAPSHOT_RESTORE"
    OUTSIDE_PROJECT_ACCESS = "OUTSIDE_PROJECT_ACCESS"
    SENSITIVE_SEND = "SENSITIVE_SEND"
    WRITE_LEASE = "WRITE_LEASE"
    WORKSPACE_READ = "WORKSPACE_READ"
    LOCAL_CONFIG_WRITE = "LOCAL_CONFIG_WRITE"


_ALLOWED: dict[str, frozenset[Capability]] = {
    "TASK": frozenset(
        {
            Capability.PROJECT_FILE_READ,
            Capability.PROJECT_FILE_WRITE,
            Capability.SHELL_EXECUTE,
            Capability.GIT_READ,
            Capability.GIT_WRITE,
            Capability.SNAPSHOT_RESTORE,
            Capability.OUTSIDE_PROJECT_ACCESS,
            Capability.SENSITIVE_SEND,
            Capability.WRITE_LEASE,
            Capability.WORKSPACE_READ,
        }
    ),
    "WORKSPACE": frozenset(
        {
            Capability.WORKSPACE_READ,
            Capability.PROJECT_FILE_READ,
            Capability.GIT_READ,
        }
    ),
    "LOCAL_CONFIG": frozenset({Capability.LOCAL_CONFIG_WRITE}),
}


@dataclass(frozen=True)
class ScopeRef:
    """命令与事件的作用域标识。

    字段名与 M0 一致，但本类只做本地校验，不替代 M0 的公共信封。
    """

    scope_type: str
    scope_id: str
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None

    def __post_init__(self) -> None:
        if self.scope_type not in SCOPE_TYPES:
            raise ValueError(f"unsupported scope_type: {self.scope_type}")
        if not self.scope_id:
            raise ValueError("scope_id is required")
        if self.scope_type == "TASK":
            if not self.task_id:
                raise ValueError("TASK scope requires task_id")
            if not self.run_id:
                raise ValueError("TASK scope requires run_id")
        if self.scope_type == "WORKSPACE" and not self.workspace_identity:
            raise ValueError("WORKSPACE scope requires workspace_identity")


class ScopeGate:
    """按作用域类型裁决能力申请。"""

    def check(self, *, scope: ScopeRef, capability: Capability) -> bool:
        if capability not in _ALLOWED[scope.scope_type]:
            raise ScopeDenied(
                f"{capability.value} is not available in scope {scope.scope_type}"
            )
        return True
