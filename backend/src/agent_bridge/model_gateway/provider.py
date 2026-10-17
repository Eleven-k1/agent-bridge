"""统一 Provider 抽象接口。

供应商差异（请求体组装、流式解析、错误原文）只允许存在于具体适配器内部；
网关与上层只消费本模块定义的统一请求与事件。
真实适配器（OpenAI 兼容 / Anthropic Messages / Z.AI）后续在本包内各自实现，
第一版闭环仅提供 FakeProvider。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from agent_bridge.model_gateway.types import ModelRequest, ProviderEvent


class ModelProvider(ABC):
    """模型供应商适配器的统一接口。

    stream 永远是流式的；失败既可以通过抛出 ProviderError（请求失败），
    也可以通过产出 FAILED 事件（流中途失败）表达。
    取消通过 request_cancel 协作完成：调用后适配器应尽快让流以 CANCELLED 事件结束。
    """

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        """流式执行一次模型请求。"""

    def request_cancel(self, operation_id: str) -> None:
        """请求取消指定操作。默认实现不支持取消（幂等空操作）。"""

    def close(self) -> None:
        """释放适配器持有的资源。默认无资源。"""
