"""命令信封的基础格式校验（M0 v0.5.1 §3）。

这是 M6 的「基础格式校验」，不是业务校验：
- 只检查必需字段存在与作用域附加字段；
- 不解释 command_type、不检查 payload 内容、不判断权限。
"""

from __future__ import annotations

from typing import Any

REQUIRED_FIELDS: tuple[str, ...] = (
    "protocol_version",
    "scope_type",
    "scope_id",
    "command_id",
    "command_idempotency_key",
    "command_request_digest",
    "issuer",
    "command_type",
    "payload",
    "issued_at",
)

VALID_SCOPE_TYPES = frozenset({"TASK", "WORKSPACE", "LOCAL_CONFIG"})


def validate_command(command: dict[str, Any]) -> list[str]:
    """返回错误列表；空列表表示通过基础格式校验。"""
    errors: list[str] = []
    if not isinstance(command, dict):
        return ["command 必须是对象"]

    for field in REQUIRED_FIELDS:
        if field not in command:
            errors.append(f"缺少必需字段 {field}")

    scope_type = command.get("scope_type")
    if scope_type is not None and scope_type not in VALID_SCOPE_TYPES:
        errors.append(f"未知 scope_type: {scope_type}")

    if scope_type == "TASK":
        if not command.get("task_id"):
            errors.append("TASK 命令缺少 task_id")
        if not command.get("run_id"):
            errors.append("TASK 命令缺少 run_id")
    elif scope_type == "WORKSPACE":
        if not command.get("workspace_identity"):
            errors.append("WORKSPACE 命令缺少 workspace_identity")
    # LOCAL_CONFIG 不要求 workspace_identity（M0 §3）

    return errors
