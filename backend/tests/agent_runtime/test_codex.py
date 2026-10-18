from pathlib import Path

from agent_bridge.agent_runtime.codex import CodexProvider
from agent_bridge.agent_runtime.types import LaunchTarget


def test_codex_builds_read_only_argv_and_preserves_prompt_as_one_argument(tmp_path):
    provider = CodexProvider()
    prompt = "Inspect the workspace\nwith this exact prompt"

    argv = provider.build_argv(
        "analysis", prompt, tmp_path, LaunchTarget(Path("codex.exe"))
    )

    assert argv[0] == "codex.exe"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[-1] == prompt
    assert " ".join(argv).count(prompt) == 1


def test_codex_builds_workspace_write_only_for_writer_role(tmp_path):
    argv = CodexProvider().build_argv(
        "writer", "implement", tmp_path, LaunchTarget(Path("codex.exe"))
    )

    assert argv[argv.index("--sandbox") + 1] == "workspace-write"


def test_codex_parses_jsonl_text_and_session_events():
    provider = CodexProvider()

    session = provider.parse_event('{"type":"thread.started","thread_id":"thread-1"}')
    delta = provider.parse_event(
        '{"type":"item.delta","delta":{"type":"text_delta","text":"partial"}}'
    )
    final = provider.parse_event(
        '{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}'
    )

    assert session is not None and session.session_id == "thread-1"
    assert delta is not None and delta.text == "partial" and not delta.is_final
    assert final is not None and final.text == "answer" and final.is_final
    assert provider.parse_event("not json") is None
