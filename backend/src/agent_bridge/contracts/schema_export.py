"""Deterministic, versioned JSON Schema exports for M0 consumers."""

from __future__ import annotations

import json
from pathlib import Path

from .envelopes import PROTOCOL_VERSION


def export_schemas(destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    schemas = {
        "command-envelope": _command_schema(),
        "event-envelope": _event_schema(),
        "model-stream-frame": _frame_schema(),
    }
    exported: dict[str, Path] = {}
    for name, schema in schemas.items():
        path = destination / f"{name}-v{PROTOCOL_VERSION}.json"
        path.write_text(
            json.dumps(schema, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        exported[name] = path
    return exported


def _base_properties() -> dict:
    return {
        "protocol_version": {"const": PROTOCOL_VERSION, "type": "string"},
        "scope_type": {"enum": ["TASK", "WORKSPACE", "LOCAL_CONFIG"], "type": "string"},
        "scope_id": {"minLength": 1, "type": "string"},
    }


def _command_schema() -> dict:
    properties = _base_properties() | {
        "command_id": {"minLength": 1, "type": "string"},
        "command_idempotency_key": {"minLength": 1, "type": "string"},
        "command_request_digest": {"minLength": 1, "type": "string"},
        "issuer": {"minLength": 1, "type": "string"},
        "command_type": {"minLength": 1, "type": "string"},
        "payload": {"type": "object"},
        "issued_at": {"minLength": 1, "type": "string"},
        "task_id": {"minLength": 1, "type": "string"},
        "run_id": {"minLength": 1, "type": "string"},
        "workspace_identity": {"minLength": 1, "type": "string"},
    }
    return _schema(
        "CommandEnvelope",
        properties,
        [
            "protocol_version", "scope_type", "scope_id", "command_id",
            "command_idempotency_key", "command_request_digest", "issuer",
            "command_type", "payload", "issued_at",
        ],
        all_of=[
            _scope_requirements("TASK", ["task_id", "run_id"]),
            _scope_requirements("WORKSPACE", ["workspace_identity"]),
            _scope_requirements("LOCAL_CONFIG", []),
        ],
    )


def _event_schema() -> dict:
    properties = _base_properties() | {
        "event_id": {"minLength": 1, "type": "string"},
        "operation_id": {"minLength": 1, "type": "string"},
        "event_deduplication_key": {"minLength": 1, "type": "string"},
        "causation_id": {"minLength": 1, "type": "string"},
        "actor": {"minLength": 1, "type": "string"},
        "event_type": {"minLength": 1, "type": "string"},
        "payload": {"type": "object"},
        "candidate_event_digest": {"minLength": 1, "type": "string"},
        "digest_algorithm_version": {"const": "sha256-canonical-json-v1", "type": "string"},
        "occurred_at": {"minLength": 1, "type": "string"},
        "integrity_ref": {"minLength": 1, "type": "string"},
        "task_id": {"minLength": 1, "type": "string"},
        "run_id": {"minLength": 1, "type": "string"},
        "workspace_identity": {"minLength": 1, "type": "string"},
        "task_event_sequence": {"minimum": 1, "type": "integer"},
        "workspace_event_sequence": {"minimum": 1, "type": "integer"},
        "local_config_event_sequence": {"minimum": 1, "type": "integer"},
    }
    return _schema(
        "EventEnvelope",
        properties,
        [
            "protocol_version", "scope_type", "scope_id", "event_id", "operation_id",
            "event_deduplication_key", "causation_id", "actor", "event_type", "payload",
            "candidate_event_digest", "digest_algorithm_version", "occurred_at", "integrity_ref",
        ],
        all_of=[
            _scope_requirements(
                "TASK",
                ["task_id", "run_id", "task_event_sequence"],
                ["workspace_event_sequence", "local_config_event_sequence"],
            ),
            _scope_requirements(
                "WORKSPACE",
                ["workspace_identity", "workspace_event_sequence"],
                ["task_event_sequence", "local_config_event_sequence"],
            ),
            _scope_requirements(
                "LOCAL_CONFIG",
                ["local_config_event_sequence"],
                ["task_event_sequence", "workspace_event_sequence"],
            ),
        ],
    )


def _frame_schema() -> dict:
    properties = {
        "scope_type": {"const": "TASK", "type": "string"},
        "task_id": {"minLength": 1, "type": "string"},
        "run_id": {"minLength": 1, "type": "string"},
        "operation_id": {"minLength": 1, "type": "string"},
        "model_fragment_sequence": {"minimum": 0, "type": "integer"},
        "frame_type": {"minLength": 1, "type": "string"},
        "content_delta_ref": {"type": "string"},
        "received_at": {"minLength": 1, "type": "string"},
    }
    return _schema("ModelStreamFrame", properties, list(properties))


def _scope_requirements(
    scope_type: str, required: list[str], forbidden: list[str] | None = None
) -> dict:
    then = {"required": required}
    if forbidden:
        then["not"] = {"anyOf": [{"required": [field]} for field in sorted(forbidden)]}
    return {
        "if": {
            "properties": {"scope_type": {"const": scope_type}},
            "required": ["scope_type"],
        },
        "then": then,
    }


def _schema(
    title: str,
    properties: dict,
    required: list[str],
    *,
    all_of: list[dict] | None = None,
) -> dict:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "title": title,
        "type": "object",
    }
    if all_of:
        schema["allOf"] = all_of
    return schema
