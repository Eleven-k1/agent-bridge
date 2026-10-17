"""工具执行门禁。

每次工具请求都必须：
1. 通过作用域门禁（M3）
2. 通过路径分类（M3）
3. 持有有效审批票据（M3）
4. 持有有效写入租约（读操作除外）（M3）
5. 先持久化 PLANNED 意图（M5）
6. 执行
7. 持久化结果事件（M5）

第一阶段只允许 FakeTool，真实 Shell/Git/写入均拒绝。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from agent_bridge.security.approval import ApprovalStore
from agent_bridge.security.lease import WriteLeaseRegistry
from agent_bridge.security.paths import PathPolicy
from agent_bridge.security.scope import Capability, ScopeGate, ScopeRef
from agent_bridge.storage.event_store import EventStore

from .errors import ToolRejected
from .fake_tool import ToolResult, ToolResultStatus

_TOOL_SPECS = {
    "file_read": {
        "capability": Capability.PROJECT_FILE_READ,
        "requires_lease": False,
        "requires_approval": False,
    },
    "file_write": {
        "capability": Capability.PROJECT_FILE_WRITE,
        "requires_lease": True,
        "requires_approval": True,
    },
    "shell_execute": {
        "capability": Capability.SHELL_EXECUTE,
        "requires_lease": False,
        "requires_approval": True,
    },
    "git_commit": {
        "capability": Capability.GIT_WRITE,
        "requires_lease": True,
        "requires_approval": True,
    },
}


@dataclass(frozen=True)
class ToolRequest:
    """一次工具请求的完整上下文。"""

    scope: ScopeRef
    operation_id: str
    tool_name: str
    target_path: str | None
    arguments: dict[str, Any]
    lease_generation: int | None
    holder: str


class ToolGate:
    """M4 的工具执行门禁，集成 M3 安全与 M5 持久化。"""

    def __init__(
        self,
        *,
        event_store: EventStore,
        approvals: ApprovalStore,
        leases: WriteLeaseRegistry,
        path_policy: PathPolicy,
        tool: Any,
        workspace_identity: str,
    ) -> None:
        self._store = event_store
        self._approvals = approvals
        self._leases = leases
        self._paths = path_policy
        self._tool = tool
        self._workspace_identity = workspace_identity
        self._scope_gate = ScopeGate()

    def execute(
        self,
        request: ToolRequest,
        *,
        ticket_id: str | None,
        now: datetime,
    ) -> ToolResult:
        """执行一次工具请求。所有门禁检查都在这里完成。"""
        if request.tool_name not in _TOOL_SPECS:
            raise ToolRejected(f"unknown tool: {request.tool_name}")

        spec = _TOOL_SPECS[request.tool_name]
        self._scope_gate.check(scope=request.scope, capability=spec["capability"])

        if request.target_path is not None:
            if spec["capability"] in (Capability.PROJECT_FILE_WRITE, Capability.GIT_WRITE):
                self._paths.require_writable(request.target_path)
            else:
                verdict = self._paths.classify(request.target_path)
                if verdict.path_class.value not in ("IN_PROJECT", "INTERNAL_DENY_WRITE"):
                    self._paths.require_writable(request.target_path)

        if spec["requires_lease"]:
            if request.lease_generation is None:
                raise ToolRejected(f"{request.tool_name} requires a write lease")
            self._leases.require_valid(
                workspace_identity=self._workspace_identity,
                generation=request.lease_generation,
                holder=request.holder,
                now=now,
            )

        if spec["requires_approval"]:
            if ticket_id is None:
                from agent_bridge.security.errors import ApprovalDenied
                raise ApprovalDenied(f"{request.tool_name} requires an approval ticket")
            self._approvals.consume(
                ticket_id=ticket_id,
                scope=request.scope,
                target_operation_id=request.operation_id,
                canonical_action_digest=self.canonical_action_digest(request),
                now=now,
            )
        elif ticket_id is not None:
            self._approvals.consume(
                ticket_id=ticket_id,
                scope=request.scope,
                target_operation_id=request.operation_id,
                canonical_action_digest=self.canonical_action_digest(request),
                now=now,
            )

        if hasattr(self._tool, "is_real") and self._tool.is_real:
            raise ToolRejected("real tool execution is not allowed in phase one")

        self._persist_planned(request, now=now)

        try:
            method = getattr(self._tool, request.tool_name, None)
            if method is None:
                raise ToolRejected(f"{request.tool_name} is not implemented")
            result = method(
                target_path=str(request.target_path) if request.target_path else None,
                arguments=request.arguments,
            )
            if result.status is ToolResultStatus.FAILED:
                self._persist_failed(request, result, now=now)
            else:
                self._persist_completed(request, result, now=now)
            return result
        except NotImplementedError as exc:
            result = ToolResult(
                status=ToolResultStatus.FAILED,
                exit_code=1,
                error_message=str(exc),
            )
            self._persist_failed(request, result, now=now)
            raise ToolRejected(str(exc)) from exc
        except Exception as exc:
            result = ToolResult(
                status=ToolResultStatus.FAILED,
                exit_code=1,
                error_message=str(exc),
            )
            self._persist_failed(request, result, now=now)
            return result

    def canonical_action_digest(self, request: ToolRequest) -> str:
        """计算请求的规范动作摘要，供审批票据绑定。"""
        from agent_bridge.contracts.envelopes import canonical_digest

        action = {
            "tool_name": request.tool_name,
            "target_path": str(request.target_path) if request.target_path else None,
            "arguments": request.arguments,
        }
        return canonical_digest(action)

    def _persist_planned(self, request: ToolRequest, *, now: datetime) -> None:
        from agent_bridge.contracts.envelopes import DIGEST_ALGORITHM_VERSION

        self._store.append(
            _make_candidate(
                scope=request.scope,
                operation_id=request.operation_id,
                event_type="TOOL_EXECUTION_PLANNED",
                payload={
                    "tool_name": request.tool_name,
                    "target_path": str(request.target_path) if request.target_path else None,
                    "arguments": request.arguments,
                },
                now=now,
            )
        )

    def _persist_completed(self, request: ToolRequest, result: ToolResult, *, now: datetime) -> None:
        self._store.append(
            _make_candidate(
                scope=request.scope,
                operation_id=request.operation_id,
                event_type="TOOL_EXECUTION_COMPLETED",
                payload={
                    "tool_name": request.tool_name,
                    "exit_code": result.exit_code,
                    "affected_paths": result.affected_paths or [],
                },
                now=now,
            )
        )

    def _persist_failed(self, request: ToolRequest, result: ToolResult, *, now: datetime) -> None:
        self._store.append(
            _make_candidate(
                scope=request.scope,
                operation_id=request.operation_id,
                event_type="TOOL_EXECUTION_FAILED",
                payload={
                    "tool_name": request.tool_name,
                    "error_message": result.error_message or result.stderr,
                },
                now=now,
            )
        )


def _make_candidate(
    *,
    scope: ScopeRef,
    operation_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> Any:
    from agent_bridge.contracts.envelopes import DIGEST_ALGORITHM_VERSION, canonical_digest

    # 返回一个鸭子类型对象，它满足 EventStore.append() 需要读取的字段。
    # 这避免了在生产代码里复制 M0 的 EventEnvelope 定义。
    class _Candidate:
        pass

    candidate = _Candidate()
    candidate.scope_type = scope.scope_type
    candidate.scope_id = scope.scope_id
    candidate.event_id = f"event-{uuid4()}"
    candidate.operation_id = operation_id
    candidate.event_deduplication_key = f"{operation_id}:{event_type}"
    candidate.causation_id = operation_id
    candidate.actor = "m4-tool-gate"
    candidate.event_type = event_type
    candidate.payload = payload
    candidate.candidate_event_digest = canonical_digest(
        {"event_type": event_type, "operation_id": operation_id, "payload": payload}
    )
    candidate.digest_algorithm_version = DIGEST_ALGORITHM_VERSION
    candidate.occurred_at = now
    candidate.task_id = scope.task_id
    candidate.run_id = scope.run_id
    candidate.workspace_identity = scope.workspace_identity
    candidate.integrity_ref = f"integrity:{candidate.event_id}"
    return candidate
