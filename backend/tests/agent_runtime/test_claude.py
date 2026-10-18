from pathlib import Path

import pytest

from agent_bridge.agent_runtime.claude import ClaudeCodeProvider
from agent_bridge.agent_runtime.types import LaunchTarget


def test_claude_uses_read_only_tool_allowlist_and_disables_mcp(tmp_path):
    argv = ClaudeCodeProvider().build_argv(
        "analysis", "review this", tmp_path, LaunchTarget(Path("claude.exe"))
    )

    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--bare" in argv


def test_claude_writer_role_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="read-only"):
        ClaudeCodeProvider().build_argv(
            "writer", "change files", tmp_path, LaunchTarget(Path("claude.exe"))
        )


def test_claude_parses_partial_and_final_stream_json():
    provider = ClaudeCodeProvider()

    partial = provider.parse_event(
        '{"type":"stream_event","session_id":"s-1","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}}'
    )
    final = provider.parse_event(
        '{"type":"result","session_id":"s-1","result":"complete answer"}'
    )

    assert partial is not None and partial.text == "hello" and not partial.is_final
    assert partial.session_id == "s-1"
    assert final is not None and final.text == "complete answer" and final.is_final
    assert provider.parse_event("{") is None
