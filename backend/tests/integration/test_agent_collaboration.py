from __future__ import annotations

import asyncio

import pytest

from agent_bridge.app import build_integrated_app
from agent_bridge.contracts.envelopes import canonical_digest
from agent_bridge.agent_runtime.coordinator import CollaborationError


def run(coro):
    return asyncio.run(coro)


def test_local_agent_inventory_is_a_formal_local_config_event(tmp_path):
    async def body():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        app = build_integrated_app(tmp_path / "events.sqlite3", workspace_root=workspace)
        await app.start()
        try:
            await app.router.handle_command({
                "scope_type": "LOCAL_CONFIG",
                "scope_id": "local-config",
                "command_id": "list-agents",
                "command_type": "LIST_LOCAL_AGENTS",
                "payload": {},
            })
            return app._event_store_raw.replay(
                scope_type="LOCAL_CONFIG", scope_id="local-config"
            )[-1]
        finally:
            await app.aclose()

    event = run(body())
    assert event.event_type == "LOCAL_AGENTS_LISTED"
    assert {agent["provider_id"] for agent in event.payload["agents"]} == {
        "codex", "claude-code", "opencode", "deepseek-harness", "zcode",
    }
    assert all("launch_target" not in agent for agent in event.payload["agents"])


def test_collaboration_rejects_unknown_agent_before_launching_processes(tmp_path):
    async def body():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        app = build_integrated_app(tmp_path / "events.sqlite3", workspace_root=workspace)
        await app.start()
        payload = {
            "prompt": "Implement a task",
            "analysis_provider_ids": ["shell", "claude-code"],
            "coordinator_provider_id": "shell",
            "writer_provider_id": "codex",
            "reviewer_provider_id": "claude-code",
            "acknowledge_workspace_access": True,
        }
        command = {
            "scope_type": "TASK", "scope_id": "task-1", "task_id": "task-1",
            "run_id": "run-1", "command_id": "start-1",
            "command_type": "START_AGENT_COLLABORATION", "payload": payload,
        }
        try:
            with pytest.raises(CollaborationError, match="unavailable"):
                await app.router.handle_command(command)
            assert app._event_store_raw.replay(scope_type="TASK", scope_id="task-1") == []
        finally:
            await app.aclose()

    run(body())
