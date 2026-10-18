import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "web-ui"))

from server.web_server import WebApp


def test_command_whitelist_and_server_side_envelope():
    command = WebApp._build_command({"type": "command", "command_type": "REAL_MODEL_RUN", "payload": {"model": "gpt", "messages": [{"role": "user", "content": "hi", "api_key": "secret"}], "scope_type": "WORKSPACE"}})
    assert command and command["command_type"] == "REAL_MODEL_RUN"
    assert command["issuer"] == "local-web"
    assert "api_key" not in json.dumps(command)
    assert WebApp._build_command({"type": "command", "command_type": "SHELL", "payload": {}}) is None
    assert WebApp._build_command({"type": "command", "command_type": "FAKE_TOOL_RUN", "payload": {"target_path": "evil.txt"}})["payload"]["target_path"] == "fake-output.txt"


def test_resume_accepts_only_supported_scopes_and_non_negative_cursors():
    assert WebApp._build_resume({"type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": 7}) == {
        "type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": 7,
    }
    assert WebApp._build_resume({"type": "resume", "scope_type": "LOCAL_CONFIG", "scope_id": "local-config", "last_event_seq": 0}) == {
        "type": "resume", "scope_type": "LOCAL_CONFIG", "scope_id": "local-config", "last_event_seq": 0,
    }
    assert WebApp._build_resume({"type": "resume", "scope_type": "WORKSPACE", "scope_id": "workspace", "last_event_seq": 0}) is None
    assert WebApp._build_resume({"type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": -1}) is None
    assert WebApp._build_resume({"type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": True}) is None


def test_health_and_static_root(tmp_path):
    async def body():
        (tmp_path / "index.html").write_text("<main>current web ui</main>", encoding="utf-8")
        app = WebApp(port=0, static_dir=tmp_path)
        await app.start()
        port = app.bound_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        data = await reader.read()
        assert b"200 OK" in data and b"Cache-Control: no-store" in data and b"current web ui" in data
        writer.close(); await writer.wait_closed()
        await app.aclose()
    asyncio.run(body())


def test_health_response():
    async def body():
        app = WebApp(port=0)
        await app.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", app.bound_port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        data = await reader.read()
        assert b"200 OK" in data and b'"ok": true' in data
        writer.close(); await writer.wait_closed()
        await app.aclose()
    asyncio.run(body())


def test_invalid_json_over_http_is_rejected():
    async def body():
        app = WebApp(port=0); await app.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", app.bound_port)
        writer.write(b"POST / HTTP/1.1\r\nHost: localhost\r\n\r\nnot-json")
        await writer.drain(); data = await reader.read()
        assert b"405 Method Not Allowed" in data
        writer.close(); await writer.wait_closed(); await app.aclose()
    asyncio.run(body())
