"""M5 命令持久化入口测试：幂等登记与冲突检测。"""

import pytest

from agent_bridge.storage.command_log import CommandLog, CommandRegistration
from agent_bridge.storage.errors import IdempotencyConflict

from .conftest import FakeCommandEnvelope


def command(**overrides) -> FakeCommandEnvelope:
    data = {
        "scope_type": "LOCAL_CONFIG",
        "scope_id": "local-config",
        "command_id": "command-1",
        "command_idempotency_key": "idem-1",
        "command_request_digest": "digest-1",
    }
    data.update(overrides)
    return FakeCommandEnvelope(**data)


@pytest.fixture
def log(tmp_path):
    with CommandLog(tmp_path / "commands.sqlite3") as opened:
        yield opened


def test_first_registration_is_accepted(log: CommandLog) -> None:
    result = log.register(command())
    assert result.status is CommandRegistration.ACCEPTED


def test_same_key_same_digest_is_a_duplicate_not_a_new_command(log: CommandLog) -> None:
    log.register(command())
    result = log.register(command(command_id="command-2"))
    assert result.status is CommandRegistration.DUPLICATE
    assert result.command_id == "command-1"


def test_same_key_different_digest_is_a_conflict(log: CommandLog) -> None:
    log.register(command())
    with pytest.raises(IdempotencyConflict):
        log.register(command(command_id="command-2", command_request_digest="digest-2"))


def test_conflict_is_recorded_as_a_fact(log: CommandLog) -> None:
    log.register(command())
    with pytest.raises(IdempotencyConflict):
        log.register(command(command_id="command-2", command_request_digest="digest-2"))
    assert log.conflicts(idempotency_key="idem-1")


def test_registration_survives_reopen(tmp_path) -> None:
    path = tmp_path / "commands.sqlite3"
    with CommandLog(path) as first:
        first.register(command())
    with CommandLog(path) as second:
        assert second.register(command()).status is CommandRegistration.DUPLICATE
