"""model_gateway 测试共享夹具。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    """pyproject 未声明 pytest-asyncio，用 asyncio.run 包装异步测试体。"""
    return asyncio.run(coro)
