from __future__ import annotations

import os
import json

import pytest

from agent_bridge.agent_runtime.registry import (
    PROVIDER_IDS,
    discover_agents,
    get_provider,
)


def test_inventory_is_deterministic_and_reports_missing_and_unadapted_tools(tmp_path):
    agents = discover_agents(path=str(tmp_path))

    assert [agent.provider_id for agent in agents] == list(PROVIDER_IDS)
    assert all(not agent.installed for agent in agents)
    assert all(agent.diagnostic for agent in agents)
    assert all(not agent.supported_roles for agent in agents if not agent.adapted)


def test_inventory_detects_supported_binary_from_explicit_path(tmp_path):
    if os.name == "nt":
        npm_root = tmp_path / "npm"
        npm_root.mkdir()
        executable = npm_root / "node.exe"
        executable.write_text("", encoding="utf-8")
        script = npm_root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        (script.parents[1] / "package.json").write_text(
            json.dumps({"name": "@openai/codex"}), encoding="utf-8"
        )
    else:
        npm_root = tmp_path
        executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)

    agents = discover_agents(path=str(npm_root))
    codex = agents[0]

    assert codex.provider_id == "codex"
    assert codex.installed is True
    assert codex.adapted is True
    assert codex.supported_roles == ("analysis", "coordinator", "writer", "reviewer")


def test_windows_cmd_shims_are_not_claimed_as_safe_native_launchers(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows launcher policy")
    shim = tmp_path / "codex.cmd"
    shim.write_text("@node arbitrary.js", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    codex = discover_agents(path=str(tmp_path))[0]

    assert codex.installed is False
    assert "安全" in codex.diagnostic


def test_unknown_provider_is_rejected():
    with pytest.raises(KeyError, match="unknown agent provider"):
        get_provider("arbitrary-command")


def test_browser_inventory_shape_does_not_expose_launch_details(tmp_path):
    agent = discover_agents(path=str(tmp_path))[0]
    browser_fields = {
        "provider_id", "display_name", "installed", "adapted",
        "supported_roles", "diagnostic",
    }

    assert set(agent.to_public_dict()) == browser_fields
    assert "argv" not in agent.to_public_dict()
    assert "environment" not in agent.to_public_dict()
    assert "cwd" not in agent.to_public_dict()
