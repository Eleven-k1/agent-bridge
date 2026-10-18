from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class LaunchTarget:
    executable: Path
    fixed_args: tuple[str, ...] = ()

    @property
    def argv_prefix(self) -> tuple[str, ...]:
        return (str(self.executable), *self.fixed_args)


@dataclass(frozen=True)
class AgentOutput:
    text: str
    channel: str = "assistant"
    is_final: bool = False
    session_id: str | None = None


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    final_text: str
    session_id: str | None
    elapsed_ms: int


class AgentProvider(Protocol):
    provider_id: str
    display_name: str
    supports_read_only: bool
    supports_write: bool

    def build_argv(
        self,
        role: str,
        prompt: str,
        workspace: Path,
        target: LaunchTarget,
    ) -> list[str]: ...

    def parse_event(self, line: str) -> AgentOutput | None: ...


@dataclass(frozen=True)
class AgentAvailability:
    provider_id: str
    display_name: str
    installed: bool
    adapted: bool
    supported_roles: tuple[str, ...]
    diagnostic: str
    launch_target: LaunchTarget | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "installed": self.installed,
            "adapted": self.adapted,
            "supported_roles": self.supported_roles,
            "diagnostic": self.diagnostic,
        }
