"""M3 脱敏测试：凭据不得进入日志、事件、错误或测试输出。"""

import pytest

from agent_bridge.config.credentials import CredentialUnavailable
from agent_bridge.security.redaction import Redactor, mask_secret

FAKE_OPENAI_KEY = "sk-" + "a1b2c3d4" * 6
FAKE_BEARER = "Bearer " + "T" * 40


@pytest.fixture
def redactor():
    return Redactor()


def test_openai_style_key_is_redacted(redactor: Redactor) -> None:
    text = redactor.redact_text(f"auth failed for key {FAKE_OPENAI_KEY}")
    assert FAKE_OPENAI_KEY not in text
    assert "[REDACTED" in text


def test_bearer_token_is_redacted(redactor: Redactor) -> None:
    assert "T" * 40 not in redactor.redact_text(f"header: {FAKE_BEARER}")


def test_private_key_block_is_redacted(redactor: Redactor) -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nAAAABBBBCCCC\n-----END RSA PRIVATE KEY-----"
    result = redactor.redact_text(block)
    assert "AAAABBBBCCCC" not in result


def test_env_assignment_value_is_redacted(redactor: Redactor) -> None:
    result = redactor.redact_text("DATABASE_PASSWORD=hunter2superlongvalue")
    assert "hunter2superlongvalue" not in result
    assert "DATABASE_PASSWORD" in result


def test_connection_string_password_is_redacted(redactor: Redactor) -> None:
    result = redactor.redact_text("postgres://user:s3cr3tpassword@localhost:5432/db")
    assert "s3cr3tpassword" not in result


def test_registered_secret_value_is_redacted_anywhere(redactor: Redactor) -> None:
    redactor.register_secret("zzz-literal-secret-value")
    assert "zzz-literal-secret-value" not in redactor.redact_text("dump: zzz-literal-secret-value")


def test_registered_secret_is_redacted_in_nested_payload(redactor: Redactor) -> None:
    redactor.register_secret("zzz-literal-secret-value")
    payload = {"outer": {"list": ["zzz-literal-secret-value"]}}
    assert "zzz-literal-secret-value" not in repr(redactor.redact_payload(payload))


def test_payload_key_named_like_a_secret_is_redacted(redactor: Redactor) -> None:
    payload = {"api_key": FAKE_OPENAI_KEY, "model": "gpt-x"}
    result = redactor.redact_payload(payload)
    assert result["api_key"] != FAKE_OPENAI_KEY
    assert result["model"] == "gpt-x"


def test_secret_key_is_redacted_even_when_value_is_short(redactor: Redactor) -> None:
    assert redactor.redact_payload({"password": "ab"})["password"] != "ab"


def test_ordinary_text_is_untouched(redactor: Redactor) -> None:
    text = "pytest exited with code 1 in src/main.py"
    assert redactor.redact_text(text) == text


def test_mask_shows_only_a_short_suffix() -> None:
    masked = mask_secret(FAKE_OPENAI_KEY)
    assert FAKE_OPENAI_KEY not in masked
    assert masked.endswith(FAKE_OPENAI_KEY[-4:])
    assert len(masked) <= 16


def test_mask_of_a_short_value_reveals_nothing() -> None:
    assert "abc" not in mask_secret("abc")


def test_registered_secret_is_not_stored_in_plaintext(redactor: Redactor) -> None:
    redactor.register_secret("zzz-literal-secret-value")
    assert "zzz-literal-secret-value" not in repr(vars(redactor))


def test_redactor_never_returns_the_input_object_for_secrets(redactor: Redactor) -> None:
    payload = {"token": FAKE_BEARER}
    result = redactor.redact_payload(payload)
    assert payload["token"] == FAKE_BEARER
    assert result["token"] != FAKE_BEARER


def test_credential_unavailable_message_does_not_echo_secret() -> None:
    secret = "sk-test-secret-123456789"
    error = CredentialUnavailable("Windows credential unavailable")
    assert secret not in str(error)
