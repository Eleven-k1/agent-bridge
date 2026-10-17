"""Fake Tool：第一阶段的占位实现。

记录意图，返回成功，但绝不碰真实文件系统、Shell 或 Git。第一阶段用它完成
纵向切片，证明门禁、审批和事件流能够端到端工作。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ToolResultStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ToolResult:
    """一次工具执行的结果。"""

    status: ToolResultStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    duration_ms: int = 0
    affected_paths: list[str] | None = None


class FakeTool:
    """第一阶段的所有工具请求都落到这里。返回成功，但什么也不做。"""

    @property
    def is_real(self) -> bool:
        """标记本执行器不产生真实副作用，供门禁检查。"""
        return False

    def file_read(self, *, target_path: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            status=ToolResultStatus.COMPLETED,
            stdout=f"[fake] would read {target_path}",
        )

    def file_write(self, *, target_path: str, arguments: dict[str, Any]) -> ToolResult:
        content = arguments.get("content")
        if content is None:
            return ToolResult(
                status=ToolResultStatus.FAILED,
                exit_code=1,
                error_message="content argument is required",
            )
        return ToolResult(
            status=ToolResultStatus.COMPLETED,
            stdout=f"[fake] would write {len(content)} bytes to {target_path}",
            affected_paths=[target_path],
        )

    def shell_execute(self, *, target_path: str | None, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError("shell_execute is not implemented in phase one")

    def git_commit(self, *, target_path: str | None, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError("git_commit is not implemented in phase one")
