"""M0 v0.5.1 transport-neutral command, event, and stream contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Generic, Mapping, TypeVar


PROTOCOL_VERSION = "0.5.1"
DIGEST_ALGORITHM_VERSION = "sha256-canonical-json-v1"


class ContractValidationError(ValueError):
    """Raised when an envelope violates frozen M0 semantics."""


class IdempotencyConflict(ContractValidationError):
    """A reused idempotency identity has a different request digest."""


class ScopeType(str, Enum):
    TASK = "TASK"
    WORKSPACE = "WORKSPACE"
    LOCAL_CONFIG = "LOCAL_CONFIG"


def canonical_digest(value: Any) -> str:
    """Returns the versioned SHA-256 digest of canonical JSON data."""

    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(value: str | None, field: str) -> None:
    if not value:
        raise ContractValidationError(f"{field} is required")


@dataclass(frozen=True, kw_only=True)
class CommandEnvelope:
    protocol_version: str
    scope_type: ScopeType
    scope_id: str
    command_id: str
    command_idempotency_key: str
    command_request_digest: str
    issuer: str
    command_type: str
    payload: Mapping[str, Any]
    issued_at: str
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.protocol_version, "protocol_version")
        _require_text(self.scope_id, "scope_id")
        for field in (
            "command_id",
            "command_idempotency_key",
            "command_request_digest",
            "issuer",
            "command_type",
            "issued_at",
        ):
            _require_text(getattr(self, field), field)
        _validate_scope(
            self.scope_type,
            self.scope_id,
            self.task_id,
            self.run_id,
            self.workspace_identity,
        )

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, kw_only=True)
class EventEnvelope:
    protocol_version: str
    scope_type: ScopeType
    scope_id: str
    event_id: str
    operation_id: str
    event_deduplication_key: str
    causation_id: str
    actor: str
    event_type: str
    payload: Mapping[str, Any]
    candidate_event_digest: str
    digest_algorithm_version: str
    occurred_at: str
    integrity_ref: str
    task_id: str | None = None
    run_id: str | None = None
    workspace_identity: str | None = None
    task_event_sequence: int | None = None
    workspace_event_sequence: int | None = None
    local_config_event_sequence: int | None = None

    def __post_init__(self) -> None:
        for field in (
            "protocol_version",
            "scope_id",
            "event_id",
            "operation_id",
            "event_deduplication_key",
            "causation_id",
            "actor",
            "event_type",
            "candidate_event_digest",
            "digest_algorithm_version",
            "occurred_at",
            "integrity_ref",
        ):
            _require_text(getattr(self, field), field)
        _validate_scope(
            self.scope_type,
            self.scope_id,
            self.task_id,
            self.run_id,
            self.workspace_identity,
        )
        expected_sequence = {
            ScopeType.TASK: "task_event_sequence",
            ScopeType.WORKSPACE: "workspace_event_sequence",
            ScopeType.LOCAL_CONFIG: "local_config_event_sequence",
        }[self.scope_type]
        sequences = {
            "task_event_sequence": self.task_event_sequence,
            "workspace_event_sequence": self.workspace_event_sequence,
            "local_config_event_sequence": self.local_config_event_sequence,
        }
        if sequences[expected_sequence] is None or sequences[expected_sequence] < 1:
            raise ContractValidationError(f"{expected_sequence} must be positive")
        if any(value is not None for name, value in sequences.items() if name != expected_sequence):
            raise ContractValidationError("formal event must have exactly one scope sequence")

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, kw_only=True)
class ModelStreamFrame:
    """A transient M2 to M6 to M7 frame, deliberately outside formal events."""

    scope_type: ScopeType
    task_id: str
    run_id: str
    operation_id: str
    model_fragment_sequence: int
    frame_type: str
    content_delta_ref: str | None = None
    received_at: str = ""

    def __post_init__(self) -> None:
        if self.scope_type is not ScopeType.TASK:
            raise ContractValidationError("model stream frames require TASK scope")
        for field in ("task_id", "run_id", "operation_id", "frame_type", "received_at"):
            _require_text(getattr(self, field), field)
        if self.model_fragment_sequence < 0:
            raise ContractValidationError("model_fragment_sequence must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


T = TypeVar("T")


@dataclass
class _IdempotencyRecord(Generic[T]):
    digest: str
    result: T


class InMemoryIdempotencyRegistry:
    """Reference semantics only; durable storage belongs to the persistence port."""

    def __init__(self) -> None:
        self._commands: dict[str, _IdempotencyRecord[Any]] = {}
        self._operations: dict[str, _IdempotencyRecord[Any]] = {}
        self._events: dict[str, _IdempotencyRecord[Any]] = {}

    def record_command(self, key: str, digest: str, result: T) -> T:
        return self._record(self._commands, key, digest, result)

    def record_operation(self, operation_id: str, digest: str, result: T) -> T:
        return self._record(self._operations, operation_id, digest, result)

    def record_event(self, key: str, digest: str, result: T) -> T:
        return self._record(self._events, key, digest, result)

    @staticmethod
    def _record(records: dict[str, _IdempotencyRecord[Any]], key: str, digest: str, result: T) -> T:
        existing = records.get(key)
        if existing is None:
            records[key] = _IdempotencyRecord(digest=digest, result=result)
            return result
        if existing.digest != digest:
            raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
        return existing.result


def _validate_scope(
    scope_type: ScopeType,
    scope_id: str,
    task_id: str | None,
    run_id: str | None,
    workspace_identity: str | None,
) -> None:
    _require_text(scope_id, "scope_id")
    if scope_type is ScopeType.TASK:
        _require_text(task_id, "task_id")
        _require_text(run_id, "run_id")
    elif scope_type is ScopeType.WORKSPACE:
        _require_text(workspace_identity, "workspace_identity")
    elif scope_type is not ScopeType.LOCAL_CONFIG:
        raise ContractValidationError("unknown scope_type")


def _clean(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value
