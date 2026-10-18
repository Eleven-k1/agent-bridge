from __future__ import annotations

import json
from pathlib import Path

from .types import AgentOutput, LaunchTarget


class CodexProvider:
    provider_id = "codex"
    display_name = "Codex CLI"
    supports_read_only = True
    supports_write = True

    def build_argv(
        self, role: str, prompt: str, workspace: Path, target: LaunchTarget
    ) -> list[str]:
        if role not in {"analysis", "coordinator", "writer", "reviewer"}:
            raise ValueError(f"unsupported Codex role: {role}")
        sandbox = "workspace-write" if role == "writer" else "read-only"
        return [
            *target.argv_prefix,
            "exec",
            "--json",
            "--sandbox",
            sandbox,
            "--cd",
            str(workspace.resolve()),
            prompt,
        ]

    def parse_event(self, line: str) -> AgentOutput | None:
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            return AgentOutput("", channel="system", session_id=thread_id if isinstance(thread_id, str) else None)
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in {"agent_message", "assistant_message"}:
            text = item.get("text")
            if isinstance(text, str):
                return AgentOutput(text, is_final=event_type == "item.completed")
        if event_type in {"item.delta", "item.updated"}:
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") in {"text_delta", "output_text_delta"}:
                text = delta.get("text")
                if isinstance(text, str):
                    return AgentOutput(text)
        if event_type == "turn.completed":
            return AgentOutput("", is_final=True)
        return None
