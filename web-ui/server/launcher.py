from __future__ import annotations

import asyncio
import json
import ntpath
import os
import shlex
import signal
import subprocess
import sys
import threading
import traceback
import webbrowser
from urllib.request import urlopen
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

ROOT = Path(__file__).resolve().parents[2]
PID_FILE = ROOT / "web-ui" / ".web-ui.pid"
STARTUP_LOCK_FILE = ROOT / "web-ui" / ".web-ui.startup.lock"
LAUNCHER_PATH = Path(__file__).resolve()
_STARTUP_THREAD_LOCK = threading.Lock()
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "web-ui"))


def _read_record(pid_file: Path) -> dict | None:
    try:
        value = json.loads(pid_file.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and isinstance(value.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def _process_lookup(pid: int) -> dict | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        script = "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d'; if ($p) { $p.CommandLine }" % pid
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True)
        command_line = result.stdout.strip()
        return {"pid": pid, "command_line": command_line} if result.returncode == 0 and command_line else None
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        raw_arguments = cmdline.read_bytes().split(b"\0")
        arguments = [argument.decode(errors="replace") for argument in raw_arguments if argument]
        return {
            "pid": pid,
            "command_line": " ".join(arguments),
            "arguments": arguments,
        }
    except OSError:
        return None


def _command_arguments(identity: dict) -> list[str]:
    arguments = identity.get("arguments")
    if isinstance(arguments, list) and all(isinstance(argument, str) for argument in arguments):
        return arguments
    command_line = str(identity.get("command_line", ""))
    try:
        arguments = shlex.split(command_line, posix=os.name != "nt")
    except ValueError:
        return []
    if os.name == "nt":
        arguments = [
            argument[1:-1] if len(argument) >= 2 and argument[0] == argument[-1] == '"' else argument
            for argument in arguments
        ]
    return arguments


def _script_argument(arguments: list[str]) -> str | None:
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in {"-c", "-m"}:
            return None
        if argument in {"-W", "-X", "--check-hash-based-pycs"}:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument
    return None


def _is_project_process(record: dict, identity: dict | None) -> bool:
    if not identity or identity.get("pid") != record.get("pid"):
        return False
    script = _script_argument(_command_arguments(identity))
    launcher = str(record.get("launcher", ""))
    return bool(script and launcher and ntpath.normcase(ntpath.normpath(script)) == ntpath.normcase(ntpath.normpath(launcher)))


def _claim_pid_file(pid_file: Path = PID_FILE, *, pid: int | None = None, launcher: str | Path = LAUNCHER_PATH) -> bool:
    record = {"pid": os.getpid() if pid is None else pid, "launcher": str(launcher)}
    try:
        descriptor = os.open(pid_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
    except BaseException:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        raise
    return True


def _release_pid_file(pid_file: Path, *, pid: int, launcher: str | Path) -> None:
    record = _read_record(pid_file)
    if record and record.get("pid") == pid and record.get("launcher") == str(launcher):
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass


def _acquire_startup_lock(lock_file: Path = STARTUP_LOCK_FILE):
    if not _STARTUP_THREAD_LOCK.acquire(blocking=False):
        return None
    stream = None
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        stream = lock_file.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        if stream is not None:
            stream.close()
        _STARTUP_THREAD_LOCK.release()
        return None


def _release_startup_lock(stream) -> None:
    try:
        if os.name == "nt":
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()
        _STARTUP_THREAD_LOCK.release()


def _healthy_service() -> bool:
    try:
        with urlopen("http://127.0.0.1:5173/health", timeout=0.8) as response:
            return response.status == 200
    except Exception:
        return False


def prepare_existing_service(pid_file: Path = PID_FILE, *, process_lookup=_process_lookup, health_check=_healthy_service) -> int | None:
    record = _read_record(pid_file)
    if not record:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        return None
    identity = process_lookup(record["pid"])
    if not _is_project_process(record, identity):
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        return None
    return record["pid"] if health_check() else -1


def _terminate_process(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return result.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _stop_project_process_locked(pid_file: Path, *, process_lookup, terminate) -> bool:
    record = _read_record(pid_file)
    if not record:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        return False
    if not _is_project_process(record, process_lookup(record["pid"])):
        return False
    stopped = terminate(record["pid"])
    if stopped:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
    return stopped


def stop_project_process(pid_file: Path = PID_FILE, *, process_lookup=_process_lookup, terminate=_terminate_process) -> bool:
    startup_lock = _acquire_startup_lock(STARTUP_LOCK_FILE)
    if startup_lock is None:
        return False
    try:
        return _stop_project_process_locked(pid_file, process_lookup=process_lookup, terminate=terminate)
    finally:
        _release_startup_lock(startup_lock)


def main() -> None:
    if "--stop" in sys.argv:
        stop_project_process()
        return
    startup_lock = _acquire_startup_lock(STARTUP_LOCK_FILE)
    if startup_lock is None:
        return
    try:
        existing = prepare_existing_service(PID_FILE)
        if existing is not None:
            if existing > 0:
                webbrowser.open("http://127.0.0.1:5173", new=2)
            else:
                (ROOT / "web-ui" / "launcher-error.log").write_text("Existing Web UI process is not healthy.\n", encoding="utf-8")
            return
        pid = os.getpid()
        if not _claim_pid_file(PID_FILE, pid=pid, launcher=LAUNCHER_PATH):
            return
    finally:
        _release_startup_lock(startup_lock)
    try:
        from agent_bridge.app import build_integrated_app
        from server.web_server import WebApp
        async def serve() -> None:
            db = Path(os.environ.get("AGENT_BRIDGE_DB_PATH", str(ROOT / "web-ui" / "agent-bridge.sqlite3")))
            backend = build_integrated_app(db_path=db, workspace_root=ROOT)
            await backend.start()
            web = WebApp(bridge_port=backend.port, bridge_token=backend.token, static_dir=ROOT / "web-ui" / "dist")
            await web.start()
            webbrowser.open("http://127.0.0.1:5173", new=2)
            try:
                await asyncio.Event().wait()
            finally:
                await web.aclose()
                await backend.aclose()
        asyncio.run(serve())
    except BaseException:
        (ROOT / "web-ui" / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        _release_pid_file(PID_FILE, pid=pid, launcher=LAUNCHER_PATH)

if __name__ == "__main__": main()
