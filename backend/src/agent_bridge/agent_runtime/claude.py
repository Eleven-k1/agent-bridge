from __future__ import annotations

import json
from pathlib import Path

from .types import AgentOutput, LaunchTarget


class ClaudeCodeProvider:
    provider_id = "claude-code"
    display_name = "Claude Code"
    supports_read_only = True
    supports_write = False

    def build_argv(
        self, role: str, prompt: str, workspace: Path, target: LaunchTarget
    ) -> list[str]:
        if role not in {"analysis", "coordinator", "reviewer"}:
            raise ValueError("Claude Code is read-only in this integration")
        return [
            *target.argv_prefix,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Glob,Grep",
            "--bare",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--add-dir",
            str(workspace.resolve()),
        ]

    def parse_event(self, line: str) -> AgentOutput | None:
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        session_id = event.get("session_id")
        if event_type == "system":
            return AgentOutput(
                "",
                channel="system",
                session_id=session_id if isinstance(session_id, str) else None,
            )
        if event_type == "stream_event":
            nested = event.get("event")
            if isinstance(nested, dict):
                delta = nested.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str):
                        return AgentOutput(
                            text,
                            session_id=session_id if isinstance(session_id, str) else None,
                        )
        if event_type == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    text = "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                        and item.get("type") == "text"
                        and isinstance(item.get("text"), str)
                    )
                    if text:
                        return AgentOutput(
                            text,
                            session_id=session_id if isinstance(session_id, str) else None,
                        )
        if event_type == "result":
            text = event.get("result")
            return AgentOutput(
                text if isinstance(text, str) else "",
                is_final=True,
                session_id=session_id if isinstance(session_id, str) else None,
            )
        return None
