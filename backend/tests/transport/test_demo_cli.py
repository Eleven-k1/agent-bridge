"""Demo CLI 入口的真实子进程测试。

验证桌面端集成的关键契约：
- stdout 首行 JSON 携带 port 与临时会话凭据；
- 凭据可用于真实 TCP 连接并完成一次 Fake 闭环；
- 父进程关闭 stdin（EOF）后服务进程自动退出，不遗留监听端口。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import LineClient, make_command, run

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _spawn_demo() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "agent_bridge.transport.demo"],
        cwd=str(BACKEND_ROOT / "src"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**_clean_env(), "PYTHONIOENCODING": "utf-8"},
    )


def _clean_env() -> dict:
    env = dict(os.environ)
    # 保证 agent_bridge 可导入：仅依赖 src 目录，不依赖测试的 pythonpath 注入
    python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(BACKEND_ROOT / "src") + (
        (";" + python_path) if python_path else ""
    )
    return env


def _read_notice(process: subprocess.Popen) -> dict:
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        raise AssertionError(f"demo 进程未输出监听通知；stderr={stderr}")
    return json.loads(line.decode("utf-8"))


class TestDemoCli:
    def test_cli_outputs_notice_and_serves_one_run(self) -> None:
        async def body(port: int, token: str) -> list[dict]:
            client = LineClient("127.0.0.1", port, token)
            await client.connect()
            await client.send(
                {
                    "type": "command",
                    "command": make_command(
                        command_type="FAKE_MODEL_RUN",
                        scope_type="TASK",
                        payload={"text_chunks": ["冒烟", "测试"], "chunk_delay_s": 0.01},
                    ),
                }
            )
            _, seen = await client.recv_until(
                lambda m: m.get("type") == "model_frame"
                and m["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
            )
            await client.close()
            return seen

        process = _spawn_demo()
        try:
            notice = _read_notice(process)
            assert notice["event"] == "listening"
            assert isinstance(notice["port"], int)
            assert len(notice["token"]) >= 32

            seen = run(body(notice["port"], notice["token"]))
            event_types = [m["event"]["event_type"] for m in seen if m["type"] == "event"]
            assert event_types[-1] == "MODEL_OPERATION_COMPLETED"
            deltas = [
                m["frame"]["content_delta"]
                for m in seen
                if m.get("type") == "model_frame"
                and m["frame"]["frame_type"] == "MODEL_DELTA_FRAME"
            ]
            assert deltas == ["冒烟", "测试"]
        finally:
            process.stdin.close()
            return_code = process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

        assert return_code == 0

    def test_cli_exits_when_stdin_closes(self) -> None:
        process = _spawn_demo()
        try:
            notice = _read_notice(process)
            assert notice["port"] > 0
        finally:
            process.stdin.close()
        return_code = process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
        assert return_code == 0
