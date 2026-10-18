from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_bridge.security.redaction import Redactor

from .types import AgentOutput, AgentProvider, AgentResult, LaunchTarget

OutputCallback = Callable[[AgentOutput], Awaitable[None] | None]
MAX_LINE_BYTES = 32_768
MAX_OUTPUT_CHARS = 64_000
TERMINATE_GRACE_SECONDS = 1.5
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsProcessTree:
    def __init__(self, handle: int):
        self.handle = handle
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._kernel32.TerminateJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    @classmethod
    def assign(cls, process: asyncio.subprocess.Process) -> "_WindowsProcessTree":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        tree = cls(int(handle))
        try:
            limits = _JobObjectExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
            transport = getattr(process, "_transport", None)
            native_process = transport.get_extra_info("subprocess") if transport else None
            native_handle = getattr(native_process, "_handle", None)
            if native_handle is None or not kernel32.AssignProcessToJobObject(
                handle, ctypes.c_void_p(int(native_handle))
            ):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            return tree
        except Exception:
            tree.close()
            raise

    def terminate(self) -> None:
        if self.handle and not self._kernel32.TerminateJobObject(self.handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = 0


class AgentProcess:
    """Runs one fixed provider command without a shell and reaps it on every exit path."""

    async def run(
        self,
        provider: AgentProvider,
        role: str,
        prompt: str,
        workspace: Path,
        on_output: OutputCallback | None = None,
        *,
        launch_target: LaunchTarget | None,
        cancel_event: asyncio.Event | None = None,
        timeout_s: float = 300,
    ) -> AgentResult:
        if launch_target is None:
            raise ValueError("launch target is unavailable")
        if len(prompt.encode("utf-8")) > 16_000:
            raise ValueError("agent prompt exceeds the 16 KB limit")
        if timeout_s <= 0:
            raise ValueError("timeout must be positive")
        cwd = workspace.resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("agent workspace must be a directory")
        argv = provider.build_argv(role, prompt, cwd, launch_target)
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("provider returned invalid argv")
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            kwargs: dict[str, object] = {"creationflags": creationflags}
        else:
            kwargs = {"start_new_session": True}
        redactor = Redactor()
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=_agent_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE_BYTES,
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"failed to launch {provider.provider_id}: {exc}") from exc
        process_tree: _WindowsProcessTree | None = None
        if os.name == "nt":
            try:
                process_tree = _WindowsProcessTree.assign(process)
            except OSError as exc:
                await _terminate_tree(process, force=True)
                raise RuntimeError("could not supervise the Windows Agent process tree") from exc
        assert process.stdout is not None and process.stderr is not None
        output_parts: list[str] = []
        final_text = ""
        session_id: str | None = None
        output_chars = 0

        async def pump_stdout() -> None:
            nonlocal final_text, session_id, output_chars
            while True:
                line = await process.stdout.readline()
                if not line:
                    return
                decoded = line.decode("utf-8", errors="replace")
                output = provider.parse_event(decoded)
                if output is None:
                    continue
                safe_text = redactor.redact_text(output.text)
                if output.session_id:
                    session_id = output.session_id[:256]
                remaining = max(0, MAX_OUTPUT_CHARS - output_chars)
                safe_text = safe_text[:remaining]
                if safe_text:
                    output_chars += len(safe_text)
                    if output.is_final:
                        final_text = safe_text
                    else:
                        output_parts.append(safe_text)
                    if on_output is not None:
                        result = on_output(
                            AgentOutput(
                                safe_text,
                                channel=output.channel,
                                is_final=output.is_final,
                                session_id=session_id,
                            )
                        )
                        if asyncio.iscoroutine(result):
                            await result

        async def pump_stderr() -> None:
            nonlocal output_chars
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                safe_text = redactor.redact_text(
                    line.decode("utf-8", errors="replace")
                ).rstrip("\r\n")
                remaining = max(0, MAX_OUTPUT_CHARS - output_chars)
                safe_text = safe_text[: min(2048, remaining)]
                if safe_text and on_output is not None:
                    output_chars += len(safe_text)
                    result = on_output(AgentOutput(safe_text, channel="stderr"))
                    if asyncio.iscoroutine(result):
                        await result

        stdout_task = asyncio.create_task(pump_stdout())
        stderr_task = asyncio.create_task(pump_stderr())
        wait_task = asyncio.create_task(process.wait())
        cancel_task = (
            asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        )
        timed_out = False
        cancelled = False
        try:
            completion_task = asyncio.gather(wait_task, stdout_task, stderr_task)
            waiters = {completion_task}
            if cancel_task is not None:
                waiters.add(cancel_task)
            done, _ = await asyncio.wait(
                waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                timed_out = True
                await _terminate_tree(process, force=True, process_tree=process_tree)
            elif cancel_task is not None and cancel_task in done and cancel_event.is_set():
                cancelled = True
                await _terminate_tree(process, force=True, process_tree=process_tree)
            else:
                try:
                    await completion_task
                except Exception:
                    await _terminate_tree(process, force=True, process_tree=process_tree)
                    raise

            try:
                await asyncio.wait_for(completion_task, timeout=TERMINATE_GRACE_SECONDS + 2)
            except asyncio.TimeoutError:
                await _terminate_tree(process, force=True, process_tree=process_tree)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            if timed_out or cancelled:
                exit_code = 124 if timed_out else 125
            else:
                exit_code = process.returncode if process.returncode is not None else 1
            combined = final_text or "".join(output_parts)
            return AgentResult(
                exit_code=exit_code,
                final_text=combined[:MAX_OUTPUT_CHARS],
                session_id=session_id,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            if process.returncode is None or process_tree is not None:
                await _terminate_tree(
                    process, force=process.returncode is None, process_tree=process_tree
                )
            for task in (stdout_task, stderr_task, wait_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
            if process_tree is not None:
                process_tree.close()


def _agent_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PROGRAMDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    )
    return {key: value for key in allowed if (value := os.environ.get(key)) is not None}


async def _terminate_tree(
    process: asyncio.subprocess.Process,
    *,
    force: bool = False,
    process_tree: _WindowsProcessTree | None = None,
) -> None:
    if os.name == "nt":
        if process_tree is not None:
            if not force and process.returncode is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    await asyncio.wait_for(
                        process.wait(), timeout=TERMINATE_GRACE_SECONDS
                    )
                except (OSError, ValueError, asyncio.TimeoutError):
                    pass
            try:
                process_tree.terminate()
            except OSError:
                await _taskkill_process_tree(process)
        else:
            await _taskkill_process_tree(process)
        if process.returncode is None:
            process.kill()
            await process.wait()
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    if process.returncode is None:
        try:
            await asyncio.wait_for(
                process.wait(), timeout=TERMINATE_GRACE_SECONDS if not force else 1.0
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.returncode is None:
                await process.wait()


async def _taskkill_process_tree(process: asyncio.subprocess.Process) -> None:
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    taskkill = system_root / "System32" / "taskkill.exe"
    if taskkill.is_file() and process.returncode is None:
        try:
            killer = await asyncio.create_subprocess_exec(
                str(taskkill),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=TERMINATE_GRACE_SECONDS)
        except (OSError, asyncio.TimeoutError):
            pass
