"""M6 本地服务与桌面通信：仅监听回环地址的 NDJSON 服务、临时会话凭据、
命令幂等登记入口、临时模型流式帧转发与正式事件发布。"""

from agent_bridge.transport.interfaces import (
    CommandRegistry,
    CommandRouter,
    CommandStatus,
    EventStore,
)
from agent_bridge.transport.memory import InMemoryCommandRegistry, InMemoryEventStore
from agent_bridge.transport.protocol import ProtocolMessage
from agent_bridge.transport.server import LoopbackServer
from agent_bridge.transport.validation import validate_command

__all__ = [
    "CommandRegistry",
    "CommandRouter",
    "CommandStatus",
    "EventStore",
    "InMemoryCommandRegistry",
    "InMemoryEventStore",
    "LoopbackServer",
    "ProtocolMessage",
    "validate_command",
]
