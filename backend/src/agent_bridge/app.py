"""本地应用装配层。

这里唯一负责跨模块连接，不定义新的公共协议：

* M1 ``Orchestrator`` 只改变任务状态并产生候选事实；
* M5 ``EventStore`` 负责事件落盘和作用域序号；
* M2 Fake 和本地 OpenAI 兼容 Provider 经统一网关、M6 帧/事件端口输出；
* M6 继续只做认证、基础校验、幂等登记和转发。

真实工具执行和审批仍未接入；现有 Fake Tool 不触碰用户项目。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from agent_bridge.agent_runtime.coordinator import AgentCollaborationCoordinator
from agent_bridge.agent_runtime.registry import discover_agents
from agent_bridge.contracts.envelopes import (
    CommandEnvelope,
    PROTOCOL_VERSION,
    ScopeType,
    canonical_digest,
)
from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.gateway import GatewayCandidate, GatewayFrame, ModelGateway
from agent_bridge.model_gateway.openai_compatible import OpenAICompatibleProvider
from agent_bridge.model_gateway.types import ModelRequest, ProviderError, TimeoutPolicy
from agent_bridge.orchestrator.core import CandidateFact, Orchestrator
from agent_bridge.security.approval import ApprovalStore
from agent_bridge.security.lease import WriteLeaseRegistry
from agent_bridge.security.paths import PathPolicy
from agent_bridge.security.scope import ScopeRef
from agent_bridge.storage.command_log import CommandLog, CommandRegistration
from agent_bridge.storage.errors import IdempotencyConflict
from agent_bridge.storage.event_store import EventStore
from agent_bridge.tools.fake_tool import FakeTool
from agent_bridge.tools.gate import ToolGate, ToolRequest
from agent_bridge.transport.interfaces import CommandStatus, RegistryResult
from agent_bridge.transport.server import LoopbackServer


DEFAULT_DB_NAME = "agent-bridge.sqlite3"
M1_COMMAND_TYPES = {
    "CREATE_TASK",
    "START_TASK",
    "PAUSE_TASK",
    "RESUME_TASK",
    "STOP_TASK",
    "RETAIN_STOPPED_MODIFICATIONS",
    "RESTORE_STOPPED_MODIFICATIONS",
    "RECOVERY_RECONCILIATION_FAILED",
    "TERMINATE_UNCOMMITTED",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _scope_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _semantic_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in (
            "protocol_version",
            "scope_type",
            "scope_id",
            "task_id",
            "run_id",
            "workspace_identity",
            "operation_id",
            "event_deduplication_key",
            "causation_id",
            "actor",
            "event_type",
            "payload",
        )
    }


def _command_from_dict(data: dict[str, Any]) -> CommandEnvelope:
    normalized = dict(data)
    normalized["scope_type"] = ScopeType(_scope_value(normalized["scope_type"]))
    return CommandEnvelope(**normalized)


class SQLiteCommandRegistry:
    """把 M6 的异步登记端口接到 M5 命令日志。"""

    def __init__(self, log: CommandLog) -> None:
        self._log = log

    async def register(self, command: dict[str, Any]) -> RegistryResult:
        try:
            result = self._log.register(_command_from_dict(command))
        except IdempotencyConflict:
            return RegistryResult(
                status=CommandStatus.IDEMPOTENCY_CONFLICT,
                existing_command_id=None,
            )
        if result.status is CommandRegistration.DUPLICATE:
            return RegistryResult(
                status=CommandStatus.REPLAY_EXISTING_RESULT,
                existing_command_id=result.command_id,
            )
        return RegistryResult(status=CommandStatus.NEW)


class SQLiteEventStore:
    """把 M6 的异步事件端口接到 M5 的同步 SQLite 权威存储。"""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    async def append(self, candidate: dict[str, Any]) -> dict[str, Any]:
        normalized = self._complete_candidate(candidate)
        record = self._store.append(SimpleNamespace(**normalized))
        return self._record_to_envelope(record)

    async def events_after(
        self, scope_type: str, scope_id: str, after_seq: int
    ) -> list[dict[str, Any]]:
        records = self._store.replay(
            scope_type=scope_type,
            scope_id=scope_id,
            after_sequence=after_seq,
        )
        return [self._record_to_envelope(record) for record in records]

    def mark_published(self, envelope: dict[str, Any]) -> None:
        self._store.mark_published(
            scope_type=str(envelope["scope_type"]),
            scope_id=str(envelope["scope_id"]),
            sequence=int(_sequence_from_envelope(envelope)),
        )

    @staticmethod
    def _record_to_envelope(record: Any) -> dict[str, Any]:
        sequence_field = {
            "TASK": "task_event_sequence",
            "WORKSPACE": "workspace_event_sequence",
            "LOCAL_CONFIG": "local_config_event_sequence",
        }[record.scope_type]
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "scope_type": record.scope_type,
            "scope_id": record.scope_id,
            "event_id": record.event_id,
            "operation_id": record.operation_id,
            "event_deduplication_key": record.event_deduplication_key,
            "causation_id": record.causation_id,
            "actor": record.actor,
            "event_type": record.event_type,
            "payload": record.payload,
            "candidate_event_digest": record.candidate_event_digest,
            "digest_algorithm_version": record.digest_algorithm_version,
            "occurred_at": record.occurred_at,
            "integrity_ref": record.integrity_ref,
            sequence_field: record.sequence,
        }
        if record.scope_type == "TASK":
            envelope.update(task_id=record.task_id, run_id=record.run_id)
        elif record.scope_type == "WORKSPACE":
            envelope["workspace_identity"] = record.workspace_identity
        return envelope

    @staticmethod
    def _complete_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(candidate)
        normalized["protocol_version"] = normalized.get("protocol_version", PROTOCOL_VERSION)
        normalized["scope_type"] = _scope_value(normalized.get("scope_type", "TASK"))
        normalized.setdefault("scope_id", normalized.get("task_id") or "local-config")
        normalized.setdefault("event_id", f"event-{uuid4().hex}")
        normalized.setdefault("operation_id", f"operation-{uuid4().hex}")
        normalized.setdefault(
            "event_deduplication_key",
            f"{normalized['operation_id']}:{normalized.get('event_type', 'UNKNOWN')}",
        )
        normalized.setdefault("causation_id", normalized["operation_id"])
        normalized.setdefault("actor", "integration")
        normalized.setdefault("payload", {})
        normalized.setdefault("occurred_at", _now())
        normalized["candidate_event_digest"] = canonical_digest(
            _semantic_candidate(normalized)
        )
        normalized["digest_algorithm_version"] = "sha256-canonical-json-v1"
        normalized.setdefault("integrity_ref", f"integrity:{normalized['event_id']}")
        return normalized


class M1EventPort:
    """同步 M1 候选端口，事件真正落盘/发布由异步路由器完成。"""

    def __init__(self) -> None:
        self._candidates: list[dict[str, Any]] = []

    def append_candidate(self, candidate: CandidateFact) -> dict[str, Any]:
        event = {
            "scope_type": "TASK",
            "scope_id": candidate.task_id,
            "task_id": candidate.task_id,
            "run_id": candidate.run_id,
            "operation_id": f"m1:{candidate.causation_id}",
            "event_deduplication_key": (
                f"m1:{candidate.causation_id}:{candidate.event_type}"
            ),
            "causation_id": candidate.causation_id,
            "actor": "M1",
            "event_type": candidate.event_type,
            "payload": {"source": "M1"},
            "occurred_at": _now(),
        }
        self._candidates.append(event)
        return event

    def drain(self) -> list[dict[str, Any]]:
        pending, self._candidates = self._candidates, []
        return pending


class IntegratedCommandRouter:
    """按命令类型连接 M1 与 M2；不改变 M6 的传输职责。"""

    def __init__(
        self,
        event_store: SQLiteEventStore,
        *,
        raw_event_store: EventStore,
        approvals: ApprovalStore,
        leases: WriteLeaseRegistry,
        tool_gate: ToolGate,
        workspace_root: Path,
        workspace_identity: str,
        real_provider: OpenAICompatibleProvider,
        agent_coordinator: AgentCollaborationCoordinator | None = None,
    ) -> None:
        self._event_store = event_store
        self._raw_event_store = raw_event_store
        self._approvals = approvals
        self._leases = leases
        self._tool_gate = tool_gate
        self._workspace_root = workspace_root
        self._workspace_identity = workspace_identity
        self._real_provider = real_provider
        self._agent_coordinator = agent_coordinator or AgentCollaborationCoordinator(workspace_root)
        self._m1_events = M1EventPort()
        self._orchestrator = Orchestrator(self._m1_events)
        self._server: LoopbackServer | None = None
        self._model_tasks: dict[str, asyncio.Task[None]] = {}
        self._gateways: dict[str, ModelGateway] = {}

    def bind(self, server: LoopbackServer) -> None:
        self._server = server

    def restore_task_events(self, events: list[Any]) -> set[tuple[str, str]]:
        return self._orchestrator.restore_task_events(events)

    def restore_agent_events(self, events: list[Any]) -> list[dict[str, Any]]:
        return self._agent_coordinator.restore(events)

    async def handle_command(self, command: dict[str, Any]) -> None:
        command_type = command.get("command_type")
        if command_type in M1_COMMAND_TYPES:
            self._orchestrator.handle(_command_from_dict(command))
            await self._publish_m1_events()
            return
        if command_type == "FAKE_MODEL_RUN":
            self._start_model_run(command)
            return
        if command_type == "REAL_MODEL_RUN":
            self._start_real_model_run(command)
            return
        if command_type == "LIST_LOCAL_MODELS":
            await self._list_local_models(command)
            return
        if command_type == "LIST_LOCAL_AGENTS":
            await self._list_local_agents(command)
            return
        if command_type == "START_AGENT_COLLABORATION":
            if self._server is None:
                raise RuntimeError("integrated router is not bound to a server")
            await self._agent_coordinator.start(
                command,
                self._server.publish_event,
                self._server.broadcast_agent_frame,
            )
            return
        if command_type == "APPROVE_AGENT_PLAN":
            payload = command.get("payload") or {}
            await self._agent_coordinator.approve_plan(
                command.get("task_id", ""),
                command.get("run_id", ""),
                payload.get("approval_digest", ""),
            )
            return
        if command_type == "CANCEL_AGENT_COLLABORATION":
            await self._agent_coordinator.cancel(
                command.get("task_id", ""),
                command.get("run_id", ""),
            )
            return
        if command_type == "FAKE_MODEL_CANCEL":
            operation_id = (command.get("payload") or {}).get("operation_id")
            gateway = self._gateways.get(operation_id)
            if gateway is not None:
                await gateway.cancel(operation_id)
            return
        if command_type == "FAKE_TOOL_RUN":
            await self._run_fake_tool(command)
            return
        # 其他命令已通过 M6/M5 登记，保持路由器不执行项目副作用。

    async def _list_local_models(self, command: dict[str, Any]) -> None:
        if self._server is None:
            raise RuntimeError("integrated router is not bound to a server")
        try:
            models = await self._real_provider.list_models()
            status, category = "available", None
        except ProviderError as exc:
            models = []
            status, category = "unavailable", exc.category.value
        await self._server.publish_event({
            "scope_type": "LOCAL_CONFIG",
            "scope_id": command["scope_id"],
            "operation_id": f"local-models:{command['command_id']}",
            "event_deduplication_key": f"local-models:{command['command_id']}",
            "causation_id": command["command_id"],
            "actor": "local-relay",
            "event_type": "LOCAL_MODELS_LISTED",
            "payload": {"models": models, "status": status, "category": category},
        })

    async def _list_local_agents(self, command: dict[str, Any]) -> None:
        if self._server is None:
            raise RuntimeError("integrated router is not bound to a server")
        agents = [agent.to_public_dict() for agent in discover_agents()]
        await self._server.publish_event({
            "scope_type": "LOCAL_CONFIG",
            "scope_id": command.get("scope_id", "local-config"),
            "operation_id": f"local-agents:{command.get('command_id', uuid4().hex)}",
            "event_deduplication_key": f"local-agents:{command.get('command_id', uuid4().hex)}",
            "causation_id": command.get("command_id", "local-agents-list"),
            "actor": "local-agent-registry",
            "event_type": "LOCAL_AGENTS_LISTED",
            "payload": {"agents": agents, "status": "available"},
        })

    def _start_real_model_run(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") or {}
        model = payload.get("model")
        messages = payload.get("messages")
        if command.get("scope_type") != "TASK" or not command.get("task_id") or not command.get("run_id"):
            raise ValueError("REAL_MODEL_RUN requires a TASK scope with task_id and run_id")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("REAL_MODEL_RUN requires a non-empty model")
        if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
            raise ValueError("REAL_MODEL_RUN requires a messages list")
        system = payload.get("system")
        if system is not None and not isinstance(system, str):
            raise ValueError("REAL_MODEL_RUN system must be a string")
        stream = payload.get("stream", True)
        if not isinstance(stream, bool):
            raise ValueError("REAL_MODEL_RUN stream must be a boolean")

        operation_id = f"op-{uuid4().hex[:12]}"
        gateway = ModelGateway(self._real_provider)
        request = ModelRequest(
            operation_id=operation_id,
            model=model.strip(),
            messages=messages,
            system=system,
            stream=stream,
            timeout=TimeoutPolicy(
                connect_timeout_s=5.0,
                first_token_timeout_s=30.0,
                stream_idle_timeout_s=30.0,
            ),
            task_id=command["task_id"],
            run_id=command["run_id"],
            agent_id="local-relay",
            max_retries=1,
        )
        self._gateways[operation_id] = gateway
        task = asyncio.create_task(self._drive_model(operation_id, gateway, request))
        self._model_tasks[operation_id] = task

    async def _publish_m1_events(self) -> None:
        if self._server is None:
            raise RuntimeError("integrated router is not bound to a server")
        for candidate in self._m1_events.drain():
            await self._server.publish_event(candidate)

    async def _run_fake_tool(self, command: dict[str, Any]) -> None:
        """运行无真实副作用的 M3/M4/M5 验证链路。"""
        if self._server is None:
            raise RuntimeError("integrated router is not bound to a server")
        payload = command.get("payload") or {}
        now = datetime.now(UTC)
        operation_id = f"tool-{uuid4().hex[:12]}"
        holder = "fake-tool-agent"
        scope = ScopeRef(
            scope_type="TASK",
            scope_id=command["scope_id"],
            task_id=command["task_id"],
            run_id=command["run_id"],
        )
        previous = self._raw_event_store.replay(
            scope_type="TASK", scope_id=command["scope_id"]
        )
        after_sequence = previous[-1].sequence if previous else 0
        lease = self._leases.acquire(
            workspace_identity=self._workspace_identity,
            scope=scope,
            holder=holder,
            granted_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        request = ToolRequest(
            scope=scope,
            operation_id=operation_id,
            tool_name="file_write",
            target_path=self._workspace_root
            / str(payload.get("target_path") or "fake-output.txt"),
            arguments={"content": str(payload.get("content") or "fake content")},
            lease_generation=lease.generation,
            holder=holder,
        )
        ticket = self._approvals.grant(
            scope=scope,
            target_operation_id=operation_id,
            canonical_action_digest=self._tool_gate.canonical_action_digest(request),
            granted_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        try:
            self._tool_gate.execute(request, ticket_id=ticket.ticket_id, now=now)
        finally:
            self._leases.release(
                workspace_identity=self._workspace_identity,
                generation=lease.generation,
                reason="fake tool workflow completed",
            )

        records = self._raw_event_store.replay(
            scope_type="TASK",
            scope_id=command["scope_id"],
            after_sequence=after_sequence,
        )
        for record in records:
            await self._server.publish_persisted_event(
                self._event_store._record_to_envelope(record)
            )

    def _start_model_run(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") or {}
        text_chunks = list(payload.get("text_chunks") or ["Fake Provider output"])
        delay = float(payload.get("chunk_delay_s", 0.01))
        operation_id = f"op-{uuid4().hex[:12]}"
        gateway = ModelGateway(
            FakeProvider(FakeScenario(text_chunks=text_chunks, chunk_delay_s=delay))
        )
        self._gateways[operation_id] = gateway
        request = ModelRequest(
            operation_id=operation_id,
            model="fake-model",
            messages=[{"role": "user", "content": "integration task"}],
            system=None,
            timeout=TimeoutPolicy(
                connect_timeout_s=5.0,
                first_token_timeout_s=5.0,
                stream_idle_timeout_s=10.0,
            ),
            task_id=command.get("task_id"),
            run_id=command.get("run_id"),
            agent_id="fake-agent",
            max_retries=1,
        )
        task = asyncio.create_task(self._drive_model(operation_id, gateway, request))
        self._model_tasks[operation_id] = task

    async def _drive_model(
        self, operation_id: str, gateway: ModelGateway, request: ModelRequest
    ) -> None:
        if self._server is None:
            raise RuntimeError("integrated router is not bound to a server")
        try:
            async for item in gateway.execute(request):
                if isinstance(item, GatewayFrame):
                    frame = dict(item.frame)
                    display_delta = frame.pop("content_delta", None)
                    # M2 的网关对象还带有便于诊断的扩展身份字段；正式
                    # app 在 M6 边界只转发冻结 Schema 的帧字段。
                    frame.pop("protocol_version", None)
                    frame.pop("scope_id", None)
                    frame["content_delta_ref"] = frame.get("content_delta_ref") or ""
                    await self._server.broadcast_frame(
                        frame, display_delta=display_delta
                    )
                elif isinstance(item, GatewayCandidate):
                    await self._server.publish_event(item.event)
        finally:
            self._model_tasks.pop(operation_id, None)
            self._gateways.pop(operation_id, None)

    async def shutdown(self) -> None:
        await self._agent_coordinator.shutdown()
        for task in self._model_tasks.values():
            task.cancel()
        if self._model_tasks:
            await asyncio.gather(*self._model_tasks.values(), return_exceptions=True)
        self._model_tasks.clear()
        self._gateways.clear()
        await self._real_provider.aclose()


@dataclass
class IntegratedApp:
    """M1/M2/M5/M6 的第一版本地应用。"""

    db_path: Path
    workspace_root: Path
    real_provider_factory: Callable[[], OpenAICompatibleProvider] = OpenAICompatibleProvider

    def __post_init__(self) -> None:
        self._event_store_raw = EventStore(self.db_path)
        self._command_log = CommandLog(self.db_path)
        self._approvals = ApprovalStore(self.db_path)
        self._leases = WriteLeaseRegistry(self.db_path)
        self._event_store = SQLiteEventStore(self._event_store_raw)
        self._real_provider = self.real_provider_factory()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        workspace_identity = f"workspace:{canonical_digest(str(self.workspace_root.resolve()))}"
        tool_gate = ToolGate(
            event_store=self._event_store_raw,
            approvals=self._approvals,
            leases=self._leases,
            path_policy=PathPolicy(workspace_root=self.workspace_root),
            tool=FakeTool(),
            workspace_identity=workspace_identity,
        )
        self.router = IntegratedCommandRouter(
            self._event_store,
            raw_event_store=self._event_store_raw,
            approvals=self._approvals,
            leases=self._leases,
            tool_gate=tool_gate,
            workspace_root=self.workspace_root,
            workspace_identity=workspace_identity,
            real_provider=self._real_provider,
        )
        self.registry = SQLiteCommandRegistry(self._command_log)
        self.server = LoopbackServer(
            router=self.router,
            registry=self.registry,
            event_store=self._event_store,
        )
        self.router.bind(self.server)

    async def start(self) -> None:
        interrupted_agent_events: list[dict[str, Any]] = []
        for scope_id in self._event_store_raw.scopes(scope_type="TASK"):
            events = self._event_store_raw.replay(scope_type="TASK", scope_id=scope_id)
            self.router.restore_task_events(events)
            interrupted_agent_events.extend(self.router.restore_agent_events(events))
        await self.server.start()
        for event in interrupted_agent_events:
            await self.server.publish_event(event)
        for scope_type, scope_id in self._event_store_raw.pending_scopes():
            for sequence in self._event_store_raw.pending_publications(
                scope_type=scope_type, scope_id=scope_id
            ):
                records = self._event_store_raw.replay(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    after_sequence=sequence - 1,
                )
                if not records or records[0].sequence != sequence:
                    raise RuntimeError(
                        f"pending event {scope_type}/{scope_id}/{sequence} is not replayable"
                    )
                await self.server.publish_persisted_event(
                    self._event_store._record_to_envelope(records[0])
                )

    async def aclose(self) -> None:
        await self.server.aclose()
        self._leases.close()
        self._approvals.close()
        self._command_log.close()
        self._event_store_raw.close()

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def token(self) -> str:
        return self.server.token


def build_integrated_app(
    db_path: Path | str | None = None,
    *,
    workspace_root: Path | str | None = None,
    real_provider_factory: Callable[[], OpenAICompatibleProvider] = OpenAICompatibleProvider,
) -> IntegratedApp:
    path = Path(db_path) if db_path is not None else Path(DEFAULT_DB_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(workspace_root) if workspace_root is not None else Path.cwd()
    return IntegratedApp(path, workspace, real_provider_factory)


def _sequence_from_envelope(envelope: dict[str, Any]) -> int:
    for field in (
        "task_event_sequence",
        "workspace_event_sequence",
        "local_config_event_sequence",
    ):
        if field in envelope:
            return int(envelope[field])
    raise ValueError("event envelope lacks a scope sequence")


async def _amain() -> None:
    configured_db = os.environ.get("AGENT_BRIDGE_DB_PATH")
    if configured_db:
        db_path = Path(configured_db)
    else:
        data_root = Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        ) / "AgentBridge"
        db_path = data_root / DEFAULT_DB_NAME
    app = build_integrated_app(db_path)
    await app.start()
    sys.stdout.write(
        json.dumps(
            {
                "event": "listening",
                "port": app.port,
                "token": app.token,
                "transport": "ndjson-tcp-v1",
            }
        )
        + "\n"
    )
    sys.stdout.flush()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def watch_stdin() -> None:
        try:
            sys.stdin.buffer.read()
        finally:
            loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=watch_stdin, daemon=True).start()
    try:
        await stop.wait()
    finally:
        await app.aclose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
