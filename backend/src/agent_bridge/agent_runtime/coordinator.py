from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from agent_bridge.contracts.envelopes import canonical_digest
from agent_bridge.security.redaction import Redactor

from .process import AgentProcess
from .registry import discover_agents, get_provider
from .types import AgentAvailability, AgentOutput, AgentResult

Emit = Callable[[dict[str, Any]], Awaitable[Any] | Any]
MAX_DIFF_CHARS = 24_000


class CollaborationError(ValueError):
    pass


@dataclass
class _Run:
    task_id: str
    run_id: str
    prompt: str
    analysis_provider_ids: tuple[str, ...]
    coordinator_provider_id: str
    writer_provider_id: str
    reviewer_provider_id: str
    emit_event: Emit
    emit_frame: Emit
    events_seen: int = 0
    plan: str | None = None
    plan_digest: str | None = None
    approved: bool = False
    writer_started: bool = False
    terminal: bool = False
    baseline_commit: str | None = None
    analysis_results: dict[str, str] = field(default_factory=dict)
    approval_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task[None] | None = None
    redactor: Redactor = field(default_factory=Redactor)


class AgentCollaborationCoordinator:
    """Coordinates real local CLI runs and records durable workflow facts."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        process: AgentProcess | Any | None = None,
        availability: tuple[AgentAvailability, ...] | None = None,
        worktree_root: Path | str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.worktree_root = (
            Path(worktree_root).resolve()
            if worktree_root is not None
            else self.workspace_root.parent / ".agent-bridge-worktrees"
        )
        self.process = process or AgentProcess()
        self.availability = {
            item.provider_id: item
            for item in (availability if availability is not None else discover_agents())
        }
        self._runs: dict[tuple[str, str], _Run] = {}
        self._closed = False

    async def start(self, command: dict[str, Any], emit_event: Emit, emit_frame: Emit) -> None:
        if self._closed:
            raise CollaborationError("agent collaboration service is shutting down")
        task_id, run_id = command.get("task_id"), command.get("run_id")
        if not isinstance(task_id, str) or not task_id or not isinstance(run_id, str) or not run_id:
            raise CollaborationError("TASK collaboration requires task_id and run_id")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,96}", task_id) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,96}", run_id
        ):
            raise CollaborationError("invalid task or run identifier")
        payload = command.get("payload")
        if not isinstance(payload, dict):
            raise CollaborationError("collaboration payload must be an object")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 16_000:
            raise CollaborationError("prompt must contain 1 to 16000 UTF-8 bytes")
        if payload.get("acknowledge_workspace_access") is not True:
            raise CollaborationError("workspace access must be acknowledged before starting agents")
        analysis_ids = payload.get("analysis_provider_ids")
        coordinator_id = payload.get("coordinator_provider_id")
        writer_id = payload.get("writer_provider_id")
        reviewer_id = payload.get("reviewer_provider_id")
        if not isinstance(analysis_ids, list) or not 2 <= len(analysis_ids) <= 4:
            raise CollaborationError("select between two and four read-only analysis agents")
        if any(not isinstance(item, str) for item in analysis_ids) or len(set(analysis_ids)) != len(analysis_ids):
            raise CollaborationError("analysis provider IDs must be unique strings")
        selected = set(analysis_ids) | {coordinator_id, writer_id, reviewer_id}
        for provider_id in selected:
            availability = self.availability.get(provider_id)
            if availability is None or not availability.installed or not availability.adapted:
                raise CollaborationError(f"agent provider is unavailable: {provider_id}")
        if not isinstance(coordinator_id, str) or "coordinator" not in self.availability[coordinator_id].supported_roles:
            raise CollaborationError("coordinator provider does not support synthesis")
        if coordinator_id not in analysis_ids:
            raise CollaborationError("coordinator must be one of the selected analysis agents")
        if writer_id != "codex" or "writer" not in self.availability[writer_id].supported_roles:
            raise CollaborationError("Codex is the only supported write agent")
        if not isinstance(reviewer_id, str) or reviewer_id == writer_id:
            raise CollaborationError("reviewer must be a different read-only provider")
        if "reviewer" not in self.availability[reviewer_id].supported_roles:
            raise CollaborationError("reviewer provider does not support read-only review")
        if not self._is_git_repository():
            raise CollaborationError("project workspace must be a Git repository")
        key = (task_id, run_id)
        if key in self._runs:
            raise CollaborationError("collaboration run already exists")

        run = _Run(
            task_id=task_id,
            run_id=run_id,
            prompt=prompt.strip(),
            analysis_provider_ids=tuple(analysis_ids),
            coordinator_provider_id=coordinator_id,
            writer_provider_id=writer_id,
            reviewer_provider_id=reviewer_id,
            emit_event=emit_event,
            emit_frame=emit_frame,
        )
        self._runs[key] = run
        run.worker = asyncio.current_task()
        await self._event(run, "AGENT_COLLABORATION_STARTED", {
            "participants": list(analysis_ids),
            "coordinator_provider_id": coordinator_id,
            "writer_provider_id": writer_id,
            "reviewer_provider_id": reviewer_id,
            "workspace_access_acknowledged": True,
        })
        await self._event(run, "AGENT_WORKSPACE_BASELINED", self._baseline_metadata())
        await self._run_analysis(run)
        run.worker = None

    async def _run_analysis(self, run: _Run) -> None:
        await asyncio.gather(
            *(self._analyze(run, provider_id) for provider_id in run.analysis_provider_ids)
        )
        results = run.analysis_results
        failed = [provider_id for provider_id in run.analysis_provider_ids if provider_id not in results]
        if run.cancel_event.is_set():
            await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", {})
            return
        if failed:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {
                "error": "one or more required analysis agents failed",
                "failed_provider_ids": failed,
            })
            return
        digest_prompt = "\n\n".join(
            f"[{provider_id}]\n{results[provider_id]}"
            for provider_id in run.analysis_provider_ids
        )
        coordinator = get_provider(run.coordinator_provider_id)
        await self._event(run, "AGENT_COORDINATION_STARTED", {
            "provider_id": run.coordinator_provider_id,
            "role": "coordinator",
        })
        try:
            result = await self.process.run(
                coordinator,
                "coordinator",
                "Synthesize the independent findings into a concise, actionable implementation plan. "
                "Include steps, risks, and validation. Do not modify files.\n\n" + digest_prompt,
                self.workspace_root,
                lambda output: self._frame(run, run.coordinator_provider_id, "coordinator", output),
                launch_target=self.availability[run.coordinator_provider_id].launch_target,
                cancel_event=run.cancel_event,
                timeout_s=300,
            )
        except Exception as exc:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {"error": _safe_error(exc)})
            return
        if run.cancel_event.is_set():
            await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", {})
            return
        if result.exit_code != 0 or not result.final_text.strip():
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {
                "error": "coordinator did not produce a valid plan",
                "exit_code": result.exit_code,
            })
            return
        run.plan = run.redactor.redact_text(result.final_text)[:12_000]
        run.plan_digest = canonical_digest({
            "task_id": run.task_id,
            "run_id": run.run_id,
            "plan": run.plan,
        })
        await self._event(run, "AGENT_PLAN_READY", {
            "provider_id": run.coordinator_provider_id,
            "plan": run.plan,
            "approval_digest": run.plan_digest,
        })

    async def _analyze(self, run: _Run, provider_id: str) -> None:
        provider = get_provider(provider_id)
        await self._event(run, "AGENT_PARTICIPANT_STATUS", {
            "provider_id": provider_id,
            "role": "analysis",
            "status": "running",
        })
        try:
            result = await self.process.run(
                provider,
                "analysis",
                "Analyze the requested software task. Read project files as needed, "
                "but do not modify files. Report findings and suggested approach.\n\n"
                + run.prompt,
                self.workspace_root,
                lambda output: self._frame(run, provider_id, "analysis", output),
                launch_target=self.availability[provider_id].launch_target,
                cancel_event=run.cancel_event,
                timeout_s=300,
            )
        except Exception as exc:
            await self._event(run, "AGENT_PARTICIPANT_STATUS", {
                "provider_id": provider_id,
                "role": "analysis",
                "status": "failed",
                "error": _safe_error(exc),
            })
            return
        if result.exit_code != 0 or not result.final_text.strip():
            await self._event(run, "AGENT_PARTICIPANT_STATUS", {
                "provider_id": provider_id,
                "role": "analysis",
                "status": "failed",
                "exit_code": result.exit_code,
            })
            return
        run.analysis_results[provider_id] = run.redactor.redact_text(result.final_text)[:8_000]
        await self._event(run, "AGENT_ANALYSIS_COMPLETED", {
            "provider_id": provider_id,
            "summary": run.analysis_results[provider_id],
            "session_id": result.session_id,
        })

    async def approve_plan(self, task_id: str, run_id: str, approval_digest: str) -> None:
        run = self._get_run(task_id, run_id)
        async with run.approval_lock:
            if run.cancel_event.is_set() or run.terminal:
                raise CollaborationError("collaboration run was cancelled or already finished")
            if run.plan is None or run.plan_digest is None:
                raise CollaborationError("plan is not ready for approval")
            if run.approved or run.writer_started:
                raise CollaborationError("plan was already approved")
            if approval_digest != run.plan_digest:
                raise CollaborationError("approval digest does not match the current plan")
            run.approved = True
            await self._event(run, "AGENT_PLAN_APPROVED", {
                "approval_digest": approval_digest,
                "writer_provider_id": run.writer_provider_id,
            })
            run.worker = asyncio.create_task(self._run_write_and_review(run))
            worker = run.worker
        await worker
        run.worker = None

    async def _run_write_and_review(self, run: _Run) -> None:
        if run.cancel_event.is_set():
            await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", {})
            return
        if run.writer_started:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {"error": "writer lease already held"})
            return
        run.writer_started = True
        try:
            worktree, branch = await asyncio.to_thread(self._create_worktree, run)
        except Exception as exc:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {"error": _safe_error(exc)})
            return
        await self._event(run, "AGENT_WRITER_STARTED", {
            "provider_id": run.writer_provider_id,
            "role": "writer",
            "worktree_path": str(worktree),
            "branch": branch,
            "baseline_commit": run.baseline_commit,
        })
        try:
            writer = await self.process.run(
                get_provider(run.writer_provider_id),
                "writer",
                "Implement the user request according to this approved plan. "
                "Make only the necessary project changes. Do not commit or push.\n\n"
                f"USER REQUEST:\n{run.prompt}\n\nAPPROVED PLAN:\n{run.plan}",
                worktree,
                lambda output: self._frame(run, run.writer_provider_id, "writer", output),
                launch_target=self.availability[run.writer_provider_id].launch_target,
                cancel_event=run.cancel_event,
                timeout_s=900,
            )
        except Exception as exc:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {
                "error": _safe_error(exc),
                **self._safe_diff_metadata(run, worktree, branch),
            })
            return
        diff = await asyncio.to_thread(
            self._diff_metadata, worktree, branch, run.baseline_commit
        )
        diff = self._redact_diff(run, diff)
        if writer.exit_code != 0:
            event_type = "AGENT_COLLABORATION_CANCELLED" if run.cancel_event.is_set() else "AGENT_COLLABORATION_FAILED"
            await self._event(run, "AGENT_WRITER_COMPLETED", {
                "provider_id": run.writer_provider_id,
                "exit_code": writer.exit_code,
                **diff,
            })
            await self._terminal(run, event_type, {
                "error": "writer process did not complete successfully",
                "exit_code": writer.exit_code,
                **diff,
            })
            return
        await self._event(run, "AGENT_WRITER_COMPLETED", {
            "provider_id": run.writer_provider_id,
            "exit_code": writer.exit_code,
            **diff,
        })
        try:
            reviewer = await self.process.run(
                get_provider(run.reviewer_provider_id),
                "reviewer",
                "Independently review the changes in this worktree. Do not modify files. "
                "Report concrete findings, severity, and file/line references.\n\n"
                f"USER REQUEST:\n{run.prompt}\n\nAPPROVED PLAN:\n{run.plan}\n\nGIT DIFF:\n{diff['diff']}",
                worktree,
                lambda output: self._frame(run, run.reviewer_provider_id, "reviewer", output),
                launch_target=self.availability[run.reviewer_provider_id].launch_target,
                cancel_event=run.cancel_event,
                timeout_s=300,
            )
        except Exception as exc:
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {
                "error": _safe_error(exc),
                **diff,
            })
            return
        if reviewer.exit_code != 0:
            if run.cancel_event.is_set():
                await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", diff)
                return
            await self._terminal(run, "AGENT_COLLABORATION_FAILED", {
                "error": "independent reviewer did not complete successfully",
                "exit_code": reviewer.exit_code,
                **diff,
            })
            return
        await self._event(run, "AGENT_REVIEW_COMPLETED", {
            "provider_id": run.reviewer_provider_id,
            "summary": run.redactor.redact_text(reviewer.final_text)[:8_000],
            "session_id": reviewer.session_id,
            **diff,
        })
        if run.cancel_event.is_set():
            await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", diff)
        else:
            await self._terminal(run, "AGENT_COLLABORATION_COMPLETED", diff)

    async def cancel(self, task_id: str, run_id: str) -> None:
        run = self._get_run(task_id, run_id)
        async with run.approval_lock:
            if run.terminal:
                return
            run.cancel_event.set()
            worker = run.worker
            if worker is None:
                await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", {})
                return
        if not worker.done():
            await worker

    async def shutdown(self) -> None:
        self._closed = True
        active = list(self._runs.values())
        for run in active:
            run.cancel_event.set()
        workers = [run.worker for run in active if run.worker is not None and not run.worker.done()]
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        for run in active:
            if not run.terminal:
                await self._terminal(run, "AGENT_COLLABORATION_CANCELLED", {})

    def restore(self, events: list[Any]) -> list[dict[str, Any]]:
        """Return terminal failure facts for runs interrupted by service shutdown."""
        runs: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            task_id = _event_value(event, "task_id")
            run_id = _event_value(event, "run_id")
            event_type = _event_value(event, "event_type")
            if not task_id or not run_id or not isinstance(event_type, str):
                continue
            key = (task_id, run_id)
            state = runs.setdefault(key, {"started": False, "terminal": False})
            if event_type == "AGENT_COLLABORATION_STARTED":
                state["started"] = True
            if event_type in {
                "AGENT_COLLABORATION_COMPLETED",
                "AGENT_COLLABORATION_FAILED",
                "AGENT_COLLABORATION_CANCELLED",
            }:
                state["terminal"] = True
        interrupted = []
        for (task_id, run_id), state in runs.items():
            if not state["started"] or state["terminal"]:
                continue
            interrupted.append({
                "scope_type": "TASK",
                "scope_id": task_id,
                "task_id": task_id,
                "run_id": run_id,
                "operation_id": f"agent-collab:{run_id}:restart",
                "event_deduplication_key": f"agent-collab:{run_id}:restart-interrupted",
                "causation_id": f"agent-collab:{run_id}",
                "actor": "local-agent-coordinator",
                "event_type": "AGENT_COLLABORATION_FAILED",
                "payload": {"error": "local service restarted before collaboration finished"},
            })
        return interrupted

    async def _frame(self, run: _Run, provider_id: str, role: str, output: AgentOutput) -> None:
        text = run.redactor.redact_text(output.text)[:2048]
        if not text:
            return
        await _invoke(run.emit_frame, {
            "type": "agent_frame",
            "frame": {
                "frame_type": "AGENT_OUTPUT_FRAME",
                "task_id": run.task_id,
                "run_id": run.run_id,
                "provider_id": provider_id,
                "role": role,
            },
            "payload": {"provider_id": provider_id, "role": role, "channel": output.channel, "delta": text},
        })

    async def _event(self, run: _Run, event_type: str, payload: dict[str, Any]) -> None:
        run.events_seen += 1
        safe_payload = run.redactor.redact_payload(payload)
        candidate = {
            "scope_type": "TASK",
            "scope_id": run.task_id,
            "task_id": run.task_id,
            "run_id": run.run_id,
            "operation_id": f"agent-collab:{run.run_id}",
            "event_deduplication_key": f"agent-collab:{run.run_id}:{run.events_seen}:{event_type}",
            "causation_id": f"agent-collab:{run.run_id}",
            "actor": "local-agent-coordinator",
            "event_type": event_type,
            "payload": safe_payload,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        await _invoke(run.emit_event, candidate)

    async def _terminal(self, run: _Run, event_type: str, payload: dict[str, Any]) -> None:
        if run.terminal:
            return
        run.terminal = True
        await self._event(run, event_type, payload)

    def _get_run(self, task_id: str, run_id: str) -> _Run:
        try:
            return self._runs[(task_id, run_id)]
        except KeyError as exc:
            raise CollaborationError("collaboration run was not found") from exc

    def _is_git_repository(self) -> bool:
        try:
            self._git("rev-parse", "--show-toplevel", cwd=self.workspace_root)
            self._git("rev-parse", "--verify", "HEAD", cwd=self.workspace_root)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    def _baseline_metadata(self) -> dict[str, Any]:
        return {
            "baseline_commit": self._git("rev-parse", "HEAD", cwd=self.workspace_root).strip(),
            "workspace_has_uncommitted_changes": bool(
                self._git("status", "--porcelain", cwd=self.workspace_root).strip()
            ),
            "uncommitted_changes_copied": False,
        }

    def _create_worktree(self, run: _Run) -> tuple[Path, str]:
        base = self._git("rev-parse", "--show-toplevel", cwd=self.workspace_root).strip()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        suffix = re.sub(r"[^A-Za-z0-9._-]", "-", f"{run.task_id}-{run.run_id}")[:80]
        branch = f"agent-bridge/{suffix}-{uuid4().hex[:8]}"
        path = self.worktree_root / f"{suffix}-{uuid4().hex[:8]}"
        subprocess.run(
            ["git", "-C", base, "worktree", "add", "-b", branch, str(path), "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        run.baseline_commit = self._git("rev-parse", "HEAD", cwd=path).strip()
        return path, branch

    def _diff_metadata(
        self, worktree: Path, branch: str, baseline: str | None
    ) -> dict[str, Any]:
        base = baseline or self._git("rev-parse", "HEAD", cwd=worktree).strip()
        subprocess.run(
            ["git", "-C", str(worktree), "add", "-N", "--", "."],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_diff = self._git(
            "diff", base, "--no-ext-diff", "--no-color", "--unified=3", cwd=worktree
        )
        full_diff = raw_diff[:MAX_DIFF_CHARS]
        return {
            "branch": branch,
            "worktree_path": str(worktree),
            "baseline_commit": base,
            "diff": full_diff,
            "diff_digest": hashlib.sha256(full_diff.encode("utf-8")).hexdigest(),
            "diff_truncated": len(raw_diff) > MAX_DIFF_CHARS,
        }

    def _safe_diff_metadata(
        self, run: _Run, worktree: Path, branch: str
    ) -> dict[str, Any]:
        return self._redact_diff(
            run, self._diff_metadata(worktree, branch, run.baseline_commit)
        )

    @staticmethod
    def _redact_diff(run: _Run, diff: dict[str, Any]) -> dict[str, Any]:
        safe = dict(diff)
        redacted = run.redactor.redact_text(str(safe["diff"]))
        safe["diff_truncated"] = bool(safe.get("diff_truncated")) or len(redacted) > MAX_DIFF_CHARS
        safe_text = redacted[:MAX_DIFF_CHARS]
        safe["diff"] = safe_text
        safe["diff_digest"] = hashlib.sha256(safe_text.encode("utf-8")).hexdigest()
        return safe

    @staticmethod
    def _git(*args: str, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout


async def _invoke(callback: Emit, value: dict[str, Any]) -> Any:
    result = callback(value)
    return await result if asyncio.iscoroutine(result) else result


def _safe_error(exc: Exception) -> str:
    return Redactor().redact_text(f"{type(exc).__name__}: {exc}")[:1000]


def _event_value(event: Any, field: str) -> Any:
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)
