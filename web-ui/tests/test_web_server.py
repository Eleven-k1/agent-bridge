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


def test_health_and_static_root():
    async def body():
        app = WebApp(port=0)
        await app.start()
        port = app.bound_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
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
