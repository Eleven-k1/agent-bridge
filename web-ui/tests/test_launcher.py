import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web-ui"))
import server.launcher as launcher


def test_stale_pid_record_is_removed(tmp_path):
    pid_file = tmp_path / ".web-ui.pid"
    pid_file.write_text(json.dumps({"pid": 123, "launcher": "project/server/launcher.py"}), encoding="utf-8")

    assert launcher.prepare_existing_service(pid_file, process_lookup=lambda pid: None) is None
    assert not pid_file.exists()


def test_valid_project_pid_reuses_existing_service(tmp_path):
    pid_file = tmp_path / ".web-ui.pid"
    pid_file.write_text(json.dumps({"pid": 123, "launcher": "project/server/launcher.py"}), encoding="utf-8")
    identity = {"pid": 123, "command_line": "python project/server/launcher.py"}

    assert launcher.prepare_existing_service(
        pid_file,
        process_lookup=lambda pid: identity,
        health_check=lambda: True,
    ) == 123
    assert pid_file.exists()


def test_stop_refuses_unrelated_process_identity(tmp_path, monkeypatch):
    pid_file = tmp_path / ".web-ui.pid"
    monkeypatch.setattr(launcher, "STARTUP_LOCK_FILE", tmp_path / ".startup.lock")
    pid_file.write_text(json.dumps({"pid": 123, "launcher": "project/server/launcher.py"}), encoding="utf-8")
    identity = {"pid": 123, "command_line": "python unrelated/service.py"}

    assert launcher.stop_project_process(pid_file, process_lookup=lambda pid: identity, terminate=lambda pid: False) is False
    assert pid_file.exists()


def test_launcher_path_in_another_argument_does_not_match_process_identity():
    record = {"pid": 123, "launcher": "project/server/launcher.py"}
    identity = {
        "pid": 123,
        "command_line": "python unrelated.py --config project/server/launcher.py",
    }

    assert launcher._is_project_process(record, identity) is False


def test_quoted_launcher_script_argument_matches_path_with_spaces():
    record = {"pid": 123, "launcher": r"D:\workspace with spaces\web-ui\server\launcher.py"}
    identity = {
        "pid": 123,
        "command_line": r'"C:\Python\pythonw.exe" "D:\workspace with spaces\web-ui\server\launcher.py"',
    }

    assert launcher._is_project_process(record, identity) is True


def test_only_one_concurrent_startup_can_claim_pid_file(tmp_path):
    claim = getattr(launcher, "_claim_pid_file", None)
    assert callable(claim), "launcher must claim its PID file atomically"

    pid_file = tmp_path / ".web-ui.pid"
    barrier = threading.Barrier(8)
    outcomes = []
    outcome_lock = threading.Lock()

    def attempt(pid):
        barrier.wait()
        won = claim(pid_file, pid=pid, launcher="project/server/launcher.py")
        with outcome_lock:
            outcomes.append(won)

    workers = [threading.Thread(target=attempt, args=(pid,)) for pid in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert json.loads(pid_file.read_text(encoding="utf-8"))["pid"] in range(8)


def test_concurrent_main_cannot_remove_pid_record_while_it_is_being_written(tmp_path, monkeypatch):
    pid_file = tmp_path / ".web-ui.pid"
    monkeypatch.setattr(launcher, "PID_FILE", pid_file)
    monkeypatch.setattr(launcher, "STARTUP_LOCK_FILE", tmp_path / ".startup.lock")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "LAUNCHER_PATH", tmp_path / "server" / "launcher.py")
    monkeypatch.setattr(launcher.sys, "argv", ["launcher.py"])
    prepare_existing = launcher.prepare_existing_service
    monkeypatch.setattr(launcher, "prepare_existing_service", lambda path: prepare_existing(path))

    pid_write_started = threading.Event()
    resume_pid_write = threading.Event()
    claim_lock = threading.Lock()
    claim_count = 0

    def pause_first_pid_write(path, *, pid, launcher):
        nonlocal claim_count
        with claim_lock:
            claim_count += 1
            should_pause = claim_count == 1
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        os.close(descriptor)
        if should_pause:
            pid_write_started.set()
            assert resume_pid_write.wait(timeout=5)
        path.write_text(json.dumps({"pid": pid, "launcher": str(launcher)}), encoding="utf-8")
        return True

    monkeypatch.setattr(launcher, "_claim_pid_file", pause_first_pid_write)
    startups = []
    thread_errors = []

    def fake_run(coroutine):
        startups.append(True)
        coroutine.close()

    monkeypatch.setattr(launcher.asyncio, "run", fake_run)

    def run_main():
        try:
            launcher.main()
        except BaseException as error:
            thread_errors.append(error)

    first = threading.Thread(target=run_main)
    second = threading.Thread(target=run_main)
    first.start()
    assert pid_write_started.wait(timeout=5)
    second.start()
    second.join(timeout=5)

    record_survived_competing_start = pid_file.exists()
    resume_pid_write.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not thread_errors
    assert record_survived_competing_start
    assert len(startups) == 1
