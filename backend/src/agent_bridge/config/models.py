from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


class ConnectionTestStatus(str, Enum):
    NEVER = "NEVER"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ConnectionCapabilities:
    model_list: bool | None = None
    streaming: bool | None = None
    tested_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_list": self.model_list,
            "streaming": self.streaming,
            "tested_at": self.tested_at,
        }


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    display_name: str
    protocol: str
    base_url: str
    model_name: str | None
    enabled: bool
    credential_ref: str
    credential_status: str
    capabilities: ConnectionCapabilities
    last_test_status: ConnectionTestStatus
    last_test_error: dict[str, str] | None
    created_at: str
    updated_at: str

    @classmethod
    def new(
        cls,
        *,
        display_name: str,
        protocol: str,
        base_url: str,
        model_name: str | None,
    ) -> "ProviderProfile":
        normalized = normalize_base_url(base_url)
        if protocol != "openai_compatible":
            raise ValueError("第一阶段只支持 openai_compatible")
        if not display_name.strip():
            raise ValueError("display_name 不能为空")
        profile_id = f"provider-{uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        return cls(
            profile_id=profile_id,
            display_name=display_name.strip(),
            protocol=protocol,
            base_url=normalized,
            model_name=model_name,
            enabled=True,
            credential_ref=f"agent-bridge/provider/{profile_id}",
            credential_status="UNCONFIGURED",
            capabilities=ConnectionCapabilities(),
            last_test_status=ConnectionTestStatus.NEVER,
            last_test_error=None,
            created_at=now,
            updated_at=now,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "enabled": self.enabled,
            "credential_ref": self.credential_ref,
            "credential_status": self.credential_status,
            "capabilities": self.capabilities.to_dict(),
            "last_test_status": self.last_test_status.value,
            "last_test_error": self.last_test_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def with_test(
        self,
        *,
        status: ConnectionTestStatus,
        capabilities: ConnectionCapabilities,
        error: dict[str, str] | None,
    ) -> "ProviderProfile":
        return replace(
            self,
            capabilities=capabilities,
            last_test_status=status,
            last_test_error=error,
            updated_at=datetime.now(UTC).isoformat(),
        )


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是 http 或 https 地址")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
