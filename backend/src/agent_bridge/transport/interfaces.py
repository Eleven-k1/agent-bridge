"""M6 的窄接口隔离层。

这些端口是 transport 与其余模块（M1 编排、M2 配置服务、M5 持久化）之间的
唯一边界。真实实现就位前，Fake 闭环使用 memory.py 中的内存实现；
集成时必须切换到对应模块的正式实现，不得在 transport 内复制业务逻辑。

设计依据 M0 v0.5.1 §5：
- M6 只做鉴权、传输校验、基础格式校验和转发；
- 命令登记与事件持久化属于持久化入口（M5 侧），这里只定义端口；
- 应用命令路由器按 scope_type 分发命令，M6 不解释业务语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class CommandStatus(str, Enum):
    """命令入口登记结果。"""

    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    REPLAY_EXISTING_RESULT = "REPLAY_EXISTING_RESULT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INVALID_COMMAND = "INVALID_COMMAND"
    ROUTER_ERROR = "ROUTER_ERROR"


class CommandRouter(Protocol):
    """应用命令路由器或等价公共端口（M0 §5）。

    实现方按 scope_type 把命令转交给 M1（TASK）、M2 配置服务（模型连接）
    或角色/设置配置服务。transport 只负责把基础校验通过的命令原样转交。

    路由器执行命令产生的一切可见结果都必须通过事件端口（publish_event）
    或帧端口（broadcast_frame）表达；command_result 回执只表达命令入口
    的登记结果，不承载业务进展。
    """

    async def handle_command(self, command: dict[str, Any]) -> None:
        """执行一条已受理的命令。"""

    async def shutdown(self) -> None:
        """取消进行中的命令处理，安全收尾。"""


@dataclass
class RegistryResult:
    """命令幂等登记的结果。"""

    status: CommandStatus
    existing_command_id: str | None = None
    existing_result: dict[str, Any] | None = None


class CommandRegistry(Protocol):
    """持久化命令入口端口（M0 §5：登记或查询 command_idempotency_key）。

    真实实现由 M5 侧提供持久化保证；内存实现仅用于 Fake 闭环。
    """

    async def register(self, command: dict[str, Any]) -> RegistryResult:
        """登记新命令或返回既有登记结果。"""


class EventStore(Protocol):
    """正式事件持久化入口端口（M0 §5：先持久化、后发布）。

    append 必须在返回前完成持久化语义（分配 event_id、序号、摘要与完整性引用）；
    transport 只广播 append 返回的完整信封，不得发布未经确认的候选事件。
    """

    async def append(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """追加候选事件，返回完整正式事件信封（幂等：同去重键返回既有事件）。"""

    async def events_after(
        self, scope_type: str, scope_id: str, after_seq: int
    ) -> list[dict[str, Any]]:
        """按作用域读取指定序号之后的正式事件，用于断线补发。"""
