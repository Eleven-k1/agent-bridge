"""敏感信息脱敏。

用于任何离开内存的文本：事件载荷、日志、错误、报告和导出。已注册的字面量以
哈希形式保存，避免脱敏器自身成为明文凭据的容器。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_PLACEHOLDER = "[REDACTED]"

# 键名判定优先于取值形态：即使取值很短，名为 password 的字段也必须脱敏。
_SECRET_KEY_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "auth",
    "cookie",
    "session",
    "private",
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED:PRIVATE_KEY]",
    ),
    (re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_\-]{16,}"), "[REDACTED:API_KEY]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), "[REDACTED:API_KEY]"),
    (re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}"), "[REDACTED:API_KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED:API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:API_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
     "[REDACTED:TOKEN]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{12,}"), r"\1 [REDACTED:TOKEN]"),
    (
        re.compile(r"(?i)\b([a-z0-9_]*(?:key|token|secret|password|passwd)[a-z0-9_]*)"
                   r"\s*[:=]\s*[\"']?([^\s\"'&;,]{6,})"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*)://([^:@/\s]+):([^@/\s]{3,})@"), r"\1://\2:[REDACTED]@"),
)


def mask_secret(value: str, *, keep: int = 4) -> str:
    """生成可安全展示的脱敏值。

    短取值不保留任何尾部字符，否则整个取值都会被暴露。
    """
    if len(value) <= keep * 2:
        return "****"
    return "****" + value[-keep:]


class _SecretHolder:
    """持有需要匹配的明文，但永不渲染它。

    脱敏器必须能匹配字面量，所以明文必然存在于内存中；这里保证它不会通过
    `repr`、`vars` 或异常回溯泄漏出去。
    """

    __slots__ = ("_value", "_digest")

    def __init__(self, value: str) -> None:
        self._value = value
        self._digest = hashlib.sha256(value.encode("utf-8")).hexdigest()

    @property
    def value(self) -> str:
        return self._value

    @property
    def label(self) -> str:
        return f"[REDACTED:{self._digest[:8]}]"

    def __repr__(self) -> str:
        return f"<secret {mask_secret(self._value)}>"

    __str__ = __repr__


class Redactor:
    """按模式和已注册字面量脱敏文本与结构化载荷。"""

    def __init__(self) -> None:
        self._secrets: list[_SecretHolder] = []

    def register_secret(self, value: str) -> None:
        """登记一个必须在任何输出中消失的字面量。"""
        if value:
            self._secrets.append(_SecretHolder(value))

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        result = text
        for secret in self._secrets:
            result = result.replace(secret.value, secret.label)
        for pattern, replacement in _PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    def redact_payload(self, payload: Any) -> Any:
        """递归脱敏，返回新对象，不修改入参。"""
        return self._walk(payload, secret_context=False)

    def _walk(self, node: Any, *, secret_context: bool) -> Any:
        if isinstance(node, dict):
            return {
                key: self._walk(value, secret_context=self._is_secret_key(key))
                for key, value in node.items()
            }
        if isinstance(node, (list, tuple)):
            walked = [self._walk(item, secret_context=secret_context) for item in node]
            return type(node)(walked) if isinstance(node, tuple) else walked
        if isinstance(node, str):
            return _PLACEHOLDER if secret_context else self.redact_text(node)
        if secret_context and node is not None:
            return _PLACEHOLDER
        return node

    @staticmethod
    def _is_secret_key(key: Any) -> bool:
        if not isinstance(key, str):
            return False
        lowered = key.lower()
        return any(hint in lowered for hint in _SECRET_KEY_HINTS)
