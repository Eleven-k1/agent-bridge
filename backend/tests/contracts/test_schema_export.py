from __future__ import annotations

from agent_bridge.contracts.schema_export import export_schemas


def test_schema_export_is_deterministic_and_versioned(tmp_path) -> None:
    first = export_schemas(tmp_path / "first")
    second = export_schemas(tmp_path / "second")

    assert first.keys() == second.keys()
    assert {path.name for path in first.values()} == {
        "command-envelope-v0.5.1.json",
        "event-envelope-v0.5.1.json",
        "model-stream-frame-v0.5.1.json",
    }
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes()


def test_model_stream_schema_excludes_task_event_sequence(tmp_path) -> None:
    schemas = export_schemas(tmp_path)
    frame = schemas["model-stream-frame"].read_text(encoding="utf-8")

    assert "model_fragment_sequence" in frame
    assert "task_event_sequence" not in frame


def test_command_schema_declares_scope_specific_required_fields(tmp_path) -> None:
    schemas = export_schemas(tmp_path)
    command = __import__("json").loads(schemas["command-envelope"].read_text(encoding="utf-8"))

    branches = command["allOf"]
    assert {branch["if"]["properties"]["scope_type"]["const"] for branch in branches} >= {
        "TASK", "WORKSPACE"
    }
    assert any(branch["then"]["required"] == ["task_id", "run_id"] for branch in branches)
    assert any(branch["then"]["required"] == ["workspace_identity"] for branch in branches)


def test_event_schema_declares_the_matching_scope_sequence(tmp_path) -> None:
    schemas = export_schemas(tmp_path)
    event = __import__("json").loads(schemas["event-envelope"].read_text(encoding="utf-8"))

    branches = event["allOf"]
    required = {
        branch["if"]["properties"]["scope_type"]["const"]: set(branch["then"]["required"])
        for branch in branches
    }
    assert "task_event_sequence" in required["TASK"]
    assert "workspace_event_sequence" in required["WORKSPACE"]
    assert "local_config_event_sequence" in required["LOCAL_CONFIG"]


def test_scope_identity_strings_reject_empty_values(tmp_path) -> None:
    schemas = export_schemas(tmp_path)
    command = __import__("json").loads(schemas["command-envelope"].read_text(encoding="utf-8"))
    event = __import__("json").loads(schemas["event-envelope"].read_text(encoding="utf-8"))

    for schema in (command, event):
        for field in ("task_id", "run_id", "workspace_identity"):
            assert schema["properties"][field]["minLength"] == 1


def test_event_schema_forbids_sequences_from_other_scopes(tmp_path) -> None:
    schemas = export_schemas(tmp_path)
    event = __import__("json").loads(schemas["event-envelope"].read_text(encoding="utf-8"))
    branches = {
        branch["if"]["properties"]["scope_type"]["const"]: branch["then"]
        for branch in event["allOf"]
    }
    sequences = {
        "TASK": "task_event_sequence",
        "WORKSPACE": "workspace_event_sequence",
        "LOCAL_CONFIG": "local_config_event_sequence",
    }

    for scope_type, own_sequence in sequences.items():
        forbidden = set(sequences.values()) - {own_sequence}
        assert branches[scope_type]["not"]["anyOf"] == [
            {"required": [sequence]} for sequence in sorted(forbidden)
        ]
