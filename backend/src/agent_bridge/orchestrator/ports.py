"""Dependency ports used by M1; implementations remain in M2-M5 modules."""

from __future__ import annotations

from typing import Protocol


class CandidateEventPort(Protocol):
    def append_candidate(self, candidate: object) -> object:
        """Persist and return a formal event through M5's authority boundary."""


class ModelGatewayPort(Protocol):
    def request_operation(self, request: object) -> object: ...

    def cancel_operation(self, operation_id: str) -> object: ...


class PolicyPort(Protocol):
    def evaluate(self, request: object) -> object: ...


class ToolPort(Protocol):
    def request_tool(self, request: object) -> object: ...

    def verify_facts(self, request: object) -> object: ...
