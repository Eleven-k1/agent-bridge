"""Fake 闭环用的内存实现：命令幂等登记入口与正式事件存储。

这是 M5 侧持久化入口的窄接口内存替身，仅用于开发联调与测试；
正式实现就位后由 transport 构造方替换注入，transport 代码不变。
事件信封字段按 M0 v0.5.1 §3/§4 组装，切换到 AI-A 的 contracts
版本化 Schema 时以接口变更单对齐（见 docs/handoffs/zcode-to-ai-a.md）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from agent_bridge.transport.interfaces import CommandStatus, RegistryResult

DIGEST_ALGORITHM_VERSION = "sha256-canonical-json-v1"

_SCOPE_SEQUENCE_FIELD = {
    "TASK": "task_event_sequence",
    "WORKSPACE": "workspace_event_sequence",
    "LOCAL_CONFIG": "local_config_event_sequence",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def candidate_digest(candidate: dict[str, Any]) -> str:
    """候选事件的语义摘要。

    覆盖语义载荷，排除 event_id、序号、occurred_at 与完整性引用——
    正常重传不会因时间戳差异被判为冲突。
    """
    semantic = {
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
    return "sha256:" + hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()


class InMemoryCommandRegistry:
    """内存命令幂等登记入口。

    同键同摘要返回既有登记（REPLAY_EXISTING_RESULT）；
    同键异摘要返回 IDEMPOTENCY_CONFLICT。
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    async def register(self, command: dict[str, Any]) -> RegistryResult:
        key = command["command_idempotency_key"]
        digest = command["command_request_digest"]
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = {
                "command_id": command["command_id"],
                "digest": digest,
                "result": None,
            }
            return RegistryResult(status=CommandStatus.NEW)
        if entry["digest"] == digest:
            return RegistryResult(
                status=CommandStatus.REPLAY_EXISTING_RESULT,
                existing_command_id=entry["command_id"],
                existing_result=entry["result"],
            )
        return RegistryResult(
            status=CommandStatus.IDEMPOTENCY_CONFLICT,
            existing_command_id=entry["command_id"],
        )


class InMemoryEventStore:
    """内存正式事件存储：原子分配序号并补全信封，同去重键幂等。"""

    def __init__(self) -> None:
        self._by_dedup_key: dict[str, dict[str, Any]] = {}
        self._streams: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def append(self, candidate: dict[str, Any]) -> dict[str, Any]:
        dedup_key = candidate.get("event_deduplication_key")
        if not dedup_key:
            raise ValueError("候选事件缺少 event_deduplication_key")
        existing = self._by_dedup_key.get(dedup_key)
        if existing is not None:
            return existing

        scope_type = candidate["scope_type"]
        scope_id = candidate["scope_id"]
        stream_key = (scope_type, scope_id)
        sequence_field = _SCOPE_SEQUENCE_FIELD[scope_type]
        stream = self._streams.setdefault(stream_key, [])

        envelope = dict(candidate)
        event_id = f"evt-{uuid.uuid4()}"
        envelope["event_id"] = event_id
        envelope[sequence_field] = len(stream) + 1
        envelope["candidate_event_digest"] = candidate_digest(candidate)
        envelope["digest_algorithm_version"] = DIGEST_ALGORITHM_VERSION
        envelope["integrity_ref"] = f"integrity:{event_id}"

        stream.append(envelope)
        self._by_dedup_key[dedup_key] = envelope
        return envelope

    async def events_after(
        self, scope_type: str, scope_id: str, after_seq: int
    ) -> list[dict[str, Any]]:
        stream = self._streams.get((scope_type, scope_id), [])
        sequence_field = _SCOPE_SEQUENCE_FIELD[scope_type]
        return [event for event in stream if event[sequence_field] > after_seq]
