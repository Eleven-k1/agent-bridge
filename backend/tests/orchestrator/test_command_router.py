from __future__ import annotations

from agent_bridge.contracts.envelopes import CommandEnvelope, ScopeType, canonical_digest
from agent_bridge.orchestrator.router import ApplicationCommandRouter


def make_command(scope_type: ScopeType) -> CommandEnvelope:
    payload = {"name": "example"}
    fields = dict(
        protocol_version="0.5.1",
        scope_type=scope_type,
        scope_id="task-1" if scope_type is ScopeType.TASK else "local-config",
        command_id=f"command-{scope_type.value}",
        command_idempotency_key=f"key-{scope_type.value}",
        command_request_digest=canonical_digest(payload),
        issuer="user-1",
        command_type="ROUTE",
        payload=payload,
        issued_at="2026-08-30T00:00:00Z",
    )
    if scope_type is ScopeType.TASK:
        fields.update(task_id="task-1", run_id="run-1")
    if scope_type is ScopeType.WORKSPACE:
        fields.update(workspace_identity="workspace-1")
    return CommandEnvelope(**fields)


def test_router_only_delegates_each_scope_to_its_designated_port() -> None:
    calls: list[str] = []
    router = ApplicationCommandRouter(
        task_handler=lambda _: calls.append("task"),
        workspace_handler=lambda _: calls.append("workspace"),
        local_config_handler=lambda _: calls.append("local-config"),
    )

    router.route(make_command(ScopeType.TASK))
    router.route(make_command(ScopeType.WORKSPACE))
    router.route(make_command(ScopeType.LOCAL_CONFIG))

    assert calls == ["task", "workspace", "local-config"]
