from __future__ import annotations

import os
import shutil
from pathlib import Path

from .claude import ClaudeCodeProvider
from .codex import CodexProvider
from .types import AgentAvailability, AgentProvider, LaunchTarget


PROVIDER_IDS = (
    "codex",
    "claude-code",
    "opencode",
    "deepseek-harness",
    "zcode",
)

_PROVIDERS: dict[str, AgentProvider] = {
    "codex": CodexProvider(),
    "claude-code": ClaudeCodeProvider(),
}
_UNADAPTED = (
    ("opencode", "OpenCode", ("opencode", "opencode.exe", "opencode.cmd")),
    ("deepseek-harness", "DeepSeek Harness", ("deepseek-harness", "deepseek-harness.cmd")),
    ("zcode", "ZCode", ("zcode", "zcode.exe", "zcode.cmd")),
)


def get_provider(provider_id: str) -> AgentProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise KeyError(f"unknown agent provider: {provider_id}") from exc


def discover_agents(path: str | None = None) -> tuple[AgentAvailability, ...]:
    search_path = os.environ.get("PATH", "") if path is None else path
    found = {
        "codex": _resolve_codex(search_path),
        "claude-code": _resolve_claude(search_path),
    }
    result: list[AgentAvailability] = []
    for provider_id, provider in _PROVIDERS.items():
        target = found[provider_id]
        result.append(
            AgentAvailability(
                provider_id=provider_id,
                display_name=provider.display_name,
                installed=target is not None,
                adapted=True,
                supported_roles=_supported_roles(provider),
                diagnostic=(
                    "可启动"
                    if target is not None
                    else "未找到可安全启动的本地 CLI；请安装官方版本并重启本地服务。"
                ),
                launch_target=target,
            )
        )
    for provider_id, name, command_names in _UNADAPTED:
        installed = _find_unadapted(command_names, search_path)
        result.append(
            AgentAvailability(
                provider_id=provider_id,
                display_name=name,
                installed=installed,
                adapted=False,
                supported_roles=(),
                diagnostic=(
                    "已检测到安装，但当前版本尚未适配。"
                    if installed
                    else "未检测到安装，且当前尚未适配。"
                ),
            )
        )
    return tuple(result)


def _supported_roles(provider: AgentProvider) -> tuple[str, ...]:
    roles = ["analysis", "coordinator", "reviewer"]
    if provider.supports_write:
        roles.insert(2, "writer")
    return tuple(roles)


def _resolve_codex(search_path: str) -> LaunchTarget | None:
    if os.name != "nt":
        executable = shutil.which("codex", path=search_path)
        return _native_target(executable)

    for directory in _path_entries(search_path):
        direct = directory / "codex.exe"
        if direct.is_file():
            return LaunchTarget(direct.resolve())
    node = next(
        (directory / "node.exe" for directory in _path_entries(search_path)
         if (directory / "node.exe").is_file()),
        None,
    )
    for directory in _npm_roots(search_path):
        script = directory / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if node is not None and _valid_npm_package(
            script.parents[1] / "package.json", "@openai/codex"
        ) and script.is_file():
            return LaunchTarget(node.resolve(), (str(script.resolve()),))
    return None


def _resolve_claude(search_path: str) -> LaunchTarget | None:
    if os.name != "nt":
        executable = shutil.which("claude", path=search_path)
        return _native_target(executable)

    for directory in _npm_roots(search_path):
        binary = directory / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        package = binary.parents[1] / "package.json"
        if binary.is_file() and _valid_npm_package(package, "@anthropic-ai/claude-code"):
            return LaunchTarget(binary.resolve())
    for directory in _path_entries(search_path):
        binary = directory / "claude.exe"
        if binary.is_file():
            return LaunchTarget(binary.resolve())
    return None


def _native_target(value: str | None) -> LaunchTarget | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_file():
        return None
    if os.name == "nt" and path.suffix.lower() not in {".exe", ".com"}:
        return None
    return LaunchTarget(path.resolve())


def _path_entries(value: str) -> tuple[Path, ...]:
    return tuple(Path(item.strip('"')) for item in value.split(os.pathsep) if item)


def _npm_roots(search_path: str) -> tuple[Path, ...]:
    roots: list[Path] = []
    for directory in _path_entries(search_path):
        if directory.name.lower() == "npm":
            roots.append(directory)
    unique = {str(path.resolve()).lower(): path for path in roots}
    return tuple(unique[key] for key in sorted(unique))


def _valid_npm_package(package_json: Path, expected_name: str) -> bool:
    try:
        import json

        package = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(package, dict) and package.get("name") == expected_name


def _find_unadapted(command_names: tuple[str, ...], search_path: str) -> bool:
    if os.name == "nt":
        return any(
            (directory / name).is_file()
            for directory in _path_entries(search_path)
            for name in command_names
        )
    return any(shutil.which(name, path=search_path) is not None for name in command_names)
