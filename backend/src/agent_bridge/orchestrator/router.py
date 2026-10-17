"""Scope-based application command router; it delegates but never executes business work."""

from __future__ import annotations

from collections.abc import Callable

from agent_bridge.contracts.envelopes import CommandEnvelope, ScopeType


class ApplicationCommandRouter:
    def __init__(
        self,
        *,
        task_handler: Callable[[CommandEnvelope], object],
        workspace_handler: Callable[[CommandEnvelope], object],
        local_config_handler: Callable[[CommandEnvelope], object],
    ) -> None:
        self._handlers = {
            ScopeType.TASK: task_handler,
            ScopeType.WORKSPACE: workspace_handler,
            ScopeType.LOCAL_CONFIG: local_config_handler,
        }

    def route(self, command: CommandEnvelope) -> object:
        return self._handlers[command.scope_type](command)
