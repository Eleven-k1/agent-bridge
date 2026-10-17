"""M2 网关的公共类型：错误归一化、超时策略、请求与 Provider 事件。

错误类别与可重试性映射对齐既往已确认提案：
auth / rate_limit / timeout / network / protocol / server / cancelled / unknown。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """统一的供应商错误类别。"""

    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    PROTOCOL = "PROTOCOL"
    SERVER = "SERVER"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


#: 默认可重试的类别：仅传输与服务端限流类失败；认证与协议错误重试无意义。
DEFAULT_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.TIMEOUT,
        ErrorCategory.NETWORK,
        ErrorCategory.SERVER,
    }
)


class TimeoutPhase(str, Enum):
    """超时发生的阶段，用于区分「连不上」「首字节慢」和「流中途卡住」。"""

    CONNECT = "connect"
    FIRST_TOKEN = "first_token"
    STREAM_IDLE = "stream_idle"


class FrameType(str, Enum):
    """M0 v0.5.1 §6 规定的临时模型流式帧类型。"""

    MODEL_STREAM_STARTED_FRAME = "MODEL_STREAM_STARTED_FRAME"
    MODEL_DELTA_FRAME = "MODEL_DELTA_FRAME"
    MODEL_STREAM_KEEPALIVE_FRAME = "MODEL_STREAM_KEEPALIVE_FRAME"
    MODEL_STREAM_END_FRAME = "MODEL_STREAM_END_FRAME"


class ProviderEventKind(str, Enum):
    """Provider 原始流式事件的种类。"""

    STARTED = "STARTED"
    DELTA = "DELTA"
    KEEPALIVE = "KEEPALIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class ProviderError(Exception):
    """归一化后的供应商错误。

    message 是脱敏后的统一说明；provider_message 仅当通过脱敏检查时保留，
    一旦检测到疑似凭据即整体丢弃，宁可少显示原始信息也不泄露。
    """

    category: ErrorCategory
    retryable: bool
    message: str
    provider_message: str | None = None
    timeout_phase: TimeoutPhase | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


@dataclass
class TimeoutPolicy:
    """三段超时：连接、首字节、流空闲。"""

    connect_timeout_s: float
    first_token_timeout_s: float
    stream_idle_timeout_s: float


@dataclass
class ModelRequest:
    """一次统一模型请求。

    模型操作属于 TASK 作用域（M0 v0.5.1 §6：流式帧 scope_type=TASK），
    因此必须携带 task_id 与 run_id。
    """

    operation_id: str
    model: str
    messages: Sequence[dict[str, Any]]
    system: str | None
    timeout: TimeoutPolicy
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    scope_type: str = "TASK"
    max_retries: int = 0
    stream: bool = True

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("operation_id 不能为空")
        if self.max_retries < 0:
            raise ValueError("max_retries 不能为负数")


@dataclass
class ProviderEvent:
    """Provider 流式输出中的单个事件。"""

    kind: ProviderEventKind
    text: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    error: ProviderError | None = None


@dataclass
class Usage:
    """Fake Provider 的用量统计。"""

    chunk_count: int = 0
    text_length: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
