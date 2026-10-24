from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_bridge.agent_runtime.coordinator import (
    AgentCollaborationCoordinator,
    CollaborationError,
)
from agent_bridge.agent_runtime.types import AgentAvailability, AgentOutput, AgentResult, LaunchTarget
from agent_bridge.contracts.envelopes import canonical_digest


class ProviderStub:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id


class ProcessStub:
    def __init__(self, *, failed_writer: bool = False, wait_for_cancel: bool = False):
        self.calls: list[tuple[str, str, str, Path]] = []
        self.active_analyses = 0
        self.max_active_analyses = 0
        self.failed_writer = failed_writer
        self.wait_for_cancel = wait_for_cancel

    async def run(
        self,
        provider,
        role,
        prompt,
        workspace,
        on_output=None,
        *,
        launch_target,
        cancel_event=None,
        timeout_s=300,
    ):
        self.calls.append((provider.provider_id, role, prompt, workspace))
        if role == "analysis":
            self.active_analyses += 1
            self.max_active_analyses = max(self.max_active_analyses, self.active_analyses)
            try:
                await asyncio.sleep(0.05)
                if on_output:
                    result = on_output(AgentOutput(f"{provider.provider_id} finding"))
                    if asyncio.iscoroutine(result):
                        await result
                return AgentResult(0, f"{provider.provider_id} finding", "session", 50)
            finally:
                self.active_analyses -= 1
        if role == "coordinator":
            return AgentResult(0, "Approved implementation plan", "coord-session", 10)
        if role == "writer":
            if self.wait_for_cancel and cancel_event is not None:
                await cancel_event.wait()
                return AgentResult(125, "cancelled", "write-session", 100)
            (workspace / "result.txt").write_text("implemented\n", encoding="utf-8")
            return AgentResult(1 if self.failed_writer else 0, "write result", "write-session", 100)
        if role == "reviewer":
            return AgentResult(0, "review passed", "review-session", 30)
        raise AssertionError(f"unexpected role {role}")


class WriterFailureWithSecretProcess(ProcessStub):
    secret = "sk-test-secret-value-1234567890"

    async def run(self, provider, role, prompt, workspace, on_output=None, **kwargs):
        if role == "writer":
            (workspace / "result.txt").write_text(
                (f"token={self.secret}\n" * 5000), encoding="utf-8"
            )
            raise RuntimeError("simulated writer failure")
        return await super().run(provider, role, prompt, workspace, on_output, **kwargs)


def make_availability():
    return (
        AgentAvailability("codex", "Codex CLI", True, True, ("analysis", "coordinator", "writer", "reviewer"), "available", LaunchTarget(Path("codex"))),
        AgentAvailability("claude-code", "Claude Code", True, True, ("analysis", "coordinator", "reviewer"), "available", LaunchTarget(Path("claude"))),
    )


def init_repository(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)


def make_command(*, run_id="run-test", provider_ids=None, prompt="Implement feature"):
    return {
        "task_id": "task-test",
        "run_id": run_id,
        "scope_type": "TASK",
        "payload": {
            "prompt": prompt,
            "analysis_provider_ids": provider_ids or ["codex", "claude-code"],
            "coordinator_provider_id": "codex",
            "writer_provider_id": "codex",
            "reviewer_provider_id": "claude-code",
            "acknowledge_workspace_access": True,
        },
    }


async def start_coordinator(tmp_path, process=None):
    workspace = tmp_path / "workspace"
    init_repository(workspace)
    events = []
    frames = []

    async def emit_event(event):
        events.append(event)

    async def emit_frame(frame):
        frames.append(frame)

    coordinator = AgentCollaborationCoordinator(
        workspace,
        process=process or ProcessStub(),
        availability=make_availability(),
        worktree_root=tmp_path / "worktrees",
    )
    await coordinator.start(make_command(), emit_event, emit_frame)
    return coordinator, events, frames


def test_read_only_agents_run_concurrently_and_plan_is_persisted_before_approval(tmp_path):
    process = ProcessStub()
    coordinator, events, frames = asyncio.run(start_coordinator(tmp_path, process))

    assert process.max_active_analyses == 2
    assert [event["event_type"] for event in events][-1] == "AGENT_PLAN_READY"
    plan = events[-1]["payload"]
    assert plan["plan"] == "Approved implementation plan"
    assert plan["approval_digest"] == canonical_digest(
        {"task_id": "task-test", "run_id": "run-test", "plan": "Approved implementation plan"}
    )
    assert not any(call[1] == "writer" for call in process.calls)
    assert any(frame["payload"]["provider_id"] == "codex" for frame in frames)
    assert any(frame["payload"]["provider_id"] == "claude-code" for frame in frames)


def test_approval_runs_one_codex_writer_in_new_worktree_then_independent_reviewer(tmp_path):
    async def body():
        process = ProcessStub()
        coordinator, events, _ = await start_coordinator(tmp_path, process)
        digest = events[-1]["payload"]["approval_digest"]
        await coordinator.approve_plan("task-test", "run-test", digest)
        return coordinator, process, events

    coordinator, process, events = asyncio.run(body())
    writer_calls = [call for call in process.calls if call[1] == "writer"]
    reviewer_calls = [call for call in process.calls if call[1] == "reviewer"]

    assert len(writer_calls) == 1
    assert writer_calls[0][0] == "codex"
    assert writer_calls[0][3] != tmp_path / "workspace"
    assert reviewer_calls and reviewer_calls[0][0] == "claude-code"
    assert (writer_calls[0][3] / "result.txt").read_text(encoding="utf-8") == "implemented\n"
    assert [event["event_type"] for event in events][-3:] == [
        "AGENT_WRITER_COMPLETED",
        "AGENT_REVIEW_COMPLETED",
        "AGENT_COLLABORATION_COMPLETED",
    ]
    assert any(event["event_type"] == "AGENT_PLAN_APPROVED" for event in events)


def test_writer_worktree_uses_head_captured_when_collaboration_started(tmp_path):
    async def body():
        process = ProcessStub()
        coordinator, events, _ = await start_coordinator(tmp_path, process)
        baseline = events[1]["payload"]["baseline_commit"]
        workspace = tmp_path / "workspace"
        (workspace / "later.txt").write_text("later commit\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(workspace), "add", "later.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(workspace), "-c", "user.email=test@example.com", "-c", "user.name=Agent Test", "commit", "-qm", "later commit"],
            check=True,
        )
        await coordinator.approve_plan("task-test", "run-test", events[-1]["payload"]["approval_digest"])
        return baseline, process, events

    baseline, process, events = asyncio.run(body())
    writer_workspace = next(call[3] for call in process.calls if call[1] == "writer")
    assert subprocess.run(
        ["git", "-C", str(writer_workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == baseline
    assert not (writer_workspace / "later.txt").exists()
    assert events[-1]["payload"]["baseline_commit"] == baseline


def test_stale_approval_is_rejected_and_failed_writer_skips_review(tmp_path):
    async def body():
        process = ProcessStub(failed_writer=True)
        coordinator, events, _ = await start_coordinator(tmp_path, process)
        with pytest.raises(CollaborationError, match="digest"):
            await coordinator.approve_plan("task-test", "run-test", "wrong")
        await coordinator.approve_plan(
            "task-test", "run-test", events[-1]["payload"]["approval_digest"]
        )
        return process, events

    process, events = asyncio.run(body())
    assert not any(call[1] == "reviewer" for call in process.calls)
    assert events[-1]["event_type"] == "AGENT_COLLABORATION_FAILED"


def test_unknown_provider_and_missing_workspace_access_ack_are_rejected(tmp_path):
    async def body():
        workspace = tmp_path / "workspace"
        init_repository(workspace)
        coordinator = AgentCollaborationCoordinator(
            workspace, process=ProcessStub(), availability=make_availability()
        )
        command = make_command(provider_ids=["unknown", "claude-code"])
        with pytest.raises(CollaborationError, match="unavailable"):
            await coordinator.start(command, lambda _: None, lambda _: None)
        command = make_command()
        command["payload"]["acknowledge_workspace_access"] = False
        with pytest.raises(CollaborationError, match="workspace access"):
            await coordinator.start(command, lambda _: None, lambda _: None)

    asyncio.run(body())


def test_cancellation_during_write_stops_the_run(tmp_path):
    async def body():
        process = ProcessStub(wait_for_cancel=True)
        coordinator, events, _ = await start_coordinator(tmp_path, process)
        approval = asyncio.create_task(
            coordinator.approve_plan(
                "task-test", "run-test", events[-1]["payload"]["approval_digest"]
            )
        )
        await asyncio.sleep(0.05)
        await coordinator.cancel("task-test", "run-test")
        await approval
        return events

    events = asyncio.run(body())
    assert events[-1]["event_type"] == "AGENT_COLLABORATION_CANCELLED"


def test_cancellation_while_waiting_for_plan_approval_is_terminal(tmp_path):
    async def body():
        coordinator, events, _ = await start_coordinator(tmp_path)
        digest = events[-1]["payload"]["approval_digest"]
        await coordinator.cancel("task-test", "run-test")
        with pytest.raises(CollaborationError, match="cancelled"):
            await coordinator.approve_plan("task-test", "run-test", digest)
        return events

    events = asyncio.run(body())
    assert events[-1]["event_type"] == "AGENT_COLLABORATION_CANCELLED"


def test_diff_reports_staged_and_unstaged_changes_once(tmp_path):
    workspace = tmp_path / "workspace"
    init_repository(workspace)
    (workspace / "README.md").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "README.md"], check=True)
    (workspace / "README.md").write_text("unstaged\n", encoding="utf-8")
    coordinator = AgentCollaborationCoordinator(workspace, availability=make_availability())

    diff = coordinator._diff_metadata(workspace, "test-branch", None)["diff"]

    assert diff.count("staged") == 1
    assert diff.count("unstaged") == 1


def test_redacted_diff_remains_within_persisted_size_limit(tmp_path):
    from agent_bridge.agent_runtime.coordinator import MAX_DIFF_CHARS, _Run

    run = _Run("task", "run", "prompt", (), "codex", "codex", "claude-code", lambda _: None, lambda _: None)
    run.redactor.register_secret("s" * 10)
    original = "s" * 10 * (MAX_DIFF_CHARS // 10)
    safe = AgentCollaborationCoordinator._redact_diff(run, {"diff": original, "diff_truncated": False})

    assert len(safe["diff"]) <= MAX_DIFF_CHARS
    assert safe["diff_truncated"] is True


def test_writer_exception_redacts_diff_in_terminal_event(tmp_path):
    async def body():
        coordinator, events, _ = await start_coordinator(tmp_path, WriterFailureWithSecretProcess())
        digest = events[-1]["payload"]["approval_digest"]
        await coordinator.approve_plan("task-test", "run-test", digest)
        return events

    events = asyncio.run(body())
    terminal = events[-1]
    assert terminal["event_type"] == "AGENT_COLLABORATION_FAILED"
    assert WriterFailureWithSecretProcess.secret not in terminal["payload"]["diff"]
    assert len(terminal["payload"]["diff"]) <= 24_000
    assert terminal["payload"]["diff_truncated"] is True
