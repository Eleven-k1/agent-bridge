"""路径分类与项目边界。

判定顺序：形式检查（数据流、保留设备名、8.3 短名）→ 解析（含链接/交汇点）→
硬拒绝集合 → 内部拒写区 → 项目内/项目外 → 敏感标记。

形式检查先行是有意的：`main.py:hidden:$DATA` 这类写法在解析后会退化成看起来
合法的项目内路径，必须在解析前拒绝。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath

from .errors import PathDenied

_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

# 8.3 短名（PROGRA~1）会绕过基于名称的规则，因此整段拒绝。
_SHORT_NAME = re.compile(r"^[^\\/:*?\"<>|]{1,6}~[0-9]{1,2}(\..{1,3})?$", re.IGNORECASE)

_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "known_hosts",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)

_SENSITIVE_SUFFIXES = frozenset(
    {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk", ".asc", ".gpg", ".crt", ".cer"}
)

# 这些位置永不提供审批入口：审批也不足以正当化读取本机凭据。
_HARD_DENIED_SEGMENTS = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".docker",
        "credentials",
        "protect",
        "vault",
    }
)

_HARD_DENIED_ROOTS = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
)

# 工具不得写入的项目内部区域。审计区归 M5，Git 内部结构可执行代码。
_INTERNAL_DIRS = ("multi-agent", ".multi-agent", ".git")


class PathClass(str, Enum):
    IN_PROJECT = "IN_PROJECT"
    OUTSIDE_PROJECT = "OUTSIDE_PROJECT"
    INTERNAL_DENY_WRITE = "INTERNAL_DENY_WRITE"
    HARD_DENIED = "HARD_DENIED"


@dataclass(frozen=True)
class PathVerdict:
    """一次路径判定的完整结论。

    `resolved_path` 供审批卡片展示完整路径；`approvable` 表示是否存在审批入口。
    """

    resolved_path: Path
    path_class: PathClass
    sensitive: bool
    approvable: bool
    reason: str


class PathPolicy:
    """把任意路径输入归类到项目边界内的固定类别。"""

    def __init__(self, *, workspace_root: Path | str) -> None:
        self._root = Path(workspace_root).resolve()

    @property
    def workspace_root(self) -> Path:
        return self._root

    def classify(self, candidate: Path | str) -> PathVerdict:
        raw = str(candidate)
        self._reject_bad_form(raw)
        resolved = self._resolve(raw)
        path_class, approvable, reason = self._locate(resolved)
        return PathVerdict(
            resolved_path=resolved,
            path_class=path_class,
            sensitive=self._is_sensitive(resolved),
            approvable=approvable,
            reason=reason,
        )

    def require_writable(self, candidate: Path | str) -> PathVerdict:
        """写入前的门禁：只有项目内非内部区域可以直接写。"""
        verdict = self.classify(candidate)
        if verdict.path_class is PathClass.IN_PROJECT:
            return verdict
        if verdict.path_class is PathClass.INTERNAL_DENY_WRITE:
            raise PathDenied(f"internal area is not writable by tools: {verdict.reason}")
        if verdict.path_class is PathClass.OUTSIDE_PROJECT:
            raise PathDenied(f"path is outside the project: {verdict.resolved_path}")
        raise PathDenied(f"path is hard denied: {verdict.reason}")

    def _reject_bad_form(self, raw: str) -> None:
        # 冒号只允许出现在盘符位置；其余冒号意味着备用数据流。
        colon_tail = raw[2:] if len(raw) > 1 and raw[1] == ":" else raw
        if ":" in colon_tail:
            raise PathDenied("alternate data stream syntax is not accepted")
        for part in PurePath(raw).parts:
            stem = part.split(".", 1)[0].upper()
            if stem in _RESERVED_DEVICE_NAMES:
                raise PathDenied(f"reserved device name is not accepted: {part}")
            if _SHORT_NAME.match(part):
                raise PathDenied(f"8.3 short name is not accepted: {part}")

    def _resolve(self, raw: str) -> Path:
        # strict=False：目标可能尚不存在（新建文件），但已存在的父级链接必须被展开。
        return Path(raw).resolve()

    def _locate(self, resolved: Path) -> tuple[PathClass, bool, str]:
        lowered = resolved.as_posix().lower()
        for root in _HARD_DENIED_ROOTS:
            if lowered == root or lowered.startswith(root + "/"):
                return PathClass.HARD_DENIED, False, f"system location {root}"
        segments = {part.lower() for part in resolved.parts}
        blocked = segments & _HARD_DENIED_SEGMENTS
        if blocked:
            return PathClass.HARD_DENIED, False, f"credential location {sorted(blocked)[0]}"

        if not self._is_within_root(resolved):
            return PathClass.OUTSIDE_PROJECT, True, "outside the workspace root"

        relative = resolved.relative_to(self._root).parts
        if relative and relative[0].lower() in _INTERNAL_DIRS:
            return PathClass.INTERNAL_DENY_WRITE, False, f"project internal area {relative[0]}"
        return PathClass.IN_PROJECT, True, "inside the workspace root"

    def _is_within_root(self, resolved: Path) -> bool:
        return resolved == self._root or self._root in resolved.parents

    def _is_sensitive(self, resolved: Path) -> bool:
        name = resolved.name.lower()
        if name in _SENSITIVE_NAMES:
            return True
        if name.startswith(".env"):
            return True
        return resolved.suffix.lower() in _SENSITIVE_SUFFIXES
