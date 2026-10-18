from __future__ import annotations

import asyncio
import ctypes
import os
import sys
from pathlib import Path
from ctypes import wintypes

import pytest

from agent_bridge.agent_runtime.process import AgentProcess
from agent_bridge.agent_runtime.types import AgentOutput, LaunchTarget


class ScriptProvider:
    provider_id = "test"
    supports_read_only = True
    supports_write = True

    def __init__(self, script: Path):
        self.script = script

    def build_argv(self, role, prompt, workspace, target):
        return [sys.executable, str(self.script), role, prompt]

    def parse_event(self, line):
        if line.startswith("TEXT:"):
            return AgentOutput(line[5:].rstrip("\r\n"), session_id="session-1")
        if line.startswith("FINAL:"):
            return AgentOutput(line[6:].rstrip("\r\n"), is_final=True, session_id="session-1")
        return None


def run(coro):
    return asyncio.run(coro)


def write_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(body, encoding="utf-8")
    return script


def windows_process_is_running(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def test_process_streams_normalized_stdout_and_stderr_without_shell(tmp_path):
    script = write_script(
        tmp_path,
        "import sys\n"
        "print('TEXT:hello', flush=True)\n"
        "print('warning', file=sys.stderr, flush=True)\n"
        "print('FINAL:world', flush=True)\n",
    )
    received: list[AgentOutput] = []

    async def body():
        return await AgentProcess().run(
            ScriptProvider(script),
            "analysis",
            "prompt with spaces\nand newlines",
            tmp_path,
            received.append,
            launch_target=LaunchTarget(Path(sys.executable)),
            timeout_s=3,
        )

    result = run(body())
    assert result.exit_code == 0
    assert result.final_text == "world"
    assert [item.text for item in received if item.channel == "assistant"] == ["hello", "world"]
    assert [item.text for item in received if item.channel == "stderr"] == ["warning"]


def test_process_timeout_terminates_child(tmp_path):
    script = write_script(tmp_path, "import time; time.sleep(10)\n")

    async def body():
        return await AgentProcess().run(
            ScriptProvider(script),
            "analysis",
            "prompt",
            tmp_path,
            launch_target=LaunchTarget(Path(sys.executable)),
            timeout_s=0.1,
        )

    result = run(body())
    assert result.exit_code != 0


def test_process_cancel_event_terminates_child(tmp_path):
    script = write_script(tmp_path, "import time; time.sleep(10)\n")
    cancelled = asyncio.Event()

    async def body():
        task = asyncio.create_task(
            AgentProcess().run(
                ScriptProvider(script),
                "analysis",
                "prompt",
                tmp_path,
                cancel_event=cancelled,
                launch_target=LaunchTarget(Path(sys.executable)),
                timeout_s=5,
            )
        )
        await asyncio.sleep(0.1)
        cancelled.set()
        return await task

    result = run(body())
    assert result.exit_code != 0


def test_process_caps_cumulative_output(tmp_path):
    script = write_script(
        tmp_path,
        "for _ in range(3):\n"
        " print('TEXT:' + 'x' * 30000, flush=True)\n",
    )

    received: list[AgentOutput] = []

    async def body():
        return await AgentProcess().run(
            ScriptProvider(script),
            "analysis",
            "prompt",
            tmp_path,
            received.append,
            launch_target=LaunchTarget(Path(sys.executable)),
            timeout_s=3,
        )

    result = run(body())
    assert len(result.final_text) <= 64_000
    assert sum(len(output.text) for output in received) <= 64_000


@pytest.mark.skipif(os.name != "nt", reason="Windows child-tree behavior")
def test_timeout_kills_descendant_after_parent_exits(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = write_script(
        tmp_path,
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(0.2)\n",
    )

    async def body():
        return await AgentProcess().run(
            ScriptProvider(script),
            "analysis",
            "prompt",
            tmp_path,
            launch_target=LaunchTarget(Path(sys.executable)),
            timeout_s=0.5,
        )

    result = run(asyncio.wait_for(body(), timeout=3))
    assert result.exit_code == 124
    assert result.elapsed_ms < 1500
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not windows_process_is_running(child_pid)


def test_process_rejects_missing_target(tmp_path):
    class MissingTargetProvider(ScriptProvider):
        pass

    async def body():
        return await AgentProcess().run(
            MissingTargetProvider(tmp_path / "missing.py"),
            "analysis",
            "prompt",
            tmp_path,
            launch_target=None,
            timeout_s=1,
        )

    with pytest.raises(ValueError, match="launch target"):
        run(body())
