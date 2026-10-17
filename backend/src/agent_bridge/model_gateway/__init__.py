"""M2 模型网关：统一 Provider 接口、Fake Provider、流式帧、取消、超时与错误归一化。"""

from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.openai_compatible import OpenAICompatibleProvider
from agent_bridge.model_gateway.gateway import (
    GatewayCandidate,
    GatewayFrame,
    ModelGateway,
)
from agent_bridge.model_gateway.provider import ModelProvider
from agent_bridge.model_gateway.types import (
    ErrorCategory,
    FrameType,
    ModelRequest,
    ProviderError,
    ProviderEvent,
    ProviderEventKind,
    TimeoutPhase,
    TimeoutPolicy,
)

__all__ = [
    "ErrorCategory",
    "FakeProvider",
    "FakeScenario",
    "FrameType",
    "GatewayCandidate",
    "GatewayFrame",
    "ModelGateway",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "ModelRequest",
    "ProviderError",
    "ProviderEvent",
    "ProviderEventKind",
    "TimeoutPhase",
    "TimeoutPolicy",
]
