from agent_bridge.config.credentials import (
    CredentialUnavailable,
    MemoryCredentialStore,
    WindowsCredentialStore,
)


def test_memory_store_round_trip_and_delete():
    store = MemoryCredentialStore()
    store.set_secret("agent-bridge/provider/p1", "secret-value")
    assert store.get_secret("agent-bridge/provider/p1") == "secret-value"
    store.delete_secret("agent-bridge/provider/p1")
    try:
        store.get_secret("agent-bridge/provider/p1")
    except CredentialUnavailable:
        pass
    else:
        raise AssertionError("deleted secret was returned")


def test_memory_store_rejects_empty_secret():
    store = MemoryCredentialStore()
    try:
        store.set_secret("ref", "")
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty credential was accepted")


def test_credential_unavailable_message_does_not_echo_secret():
    secret = "sk-test-secret-123456789"
    error = CredentialUnavailable("Windows credential unavailable")
    text = str(error)
    assert secret not in text
    assert "Windows credential unavailable" in text


def test_prompt_and_set_uses_prompt_result_without_returning_secret(monkeypatch):
    store = WindowsCredentialStore()
    captured: dict[str, tuple[str, str]] = {}

    monkeypatch.setattr(
        "agent_bridge.config.credentials._prompt_windows_secret",
        lambda display_name: "secret-from-prompt",
    )
    monkeypatch.setattr(
        store,
        "set_secret",
        lambda credential_ref, secret: captured.setdefault(
            "call", (credential_ref, secret)
        ),
    )

    result = store.prompt_and_set("agent-bridge/provider/prompt", "Prompt Name")

    assert result is None
    assert captured["call"] == ("agent-bridge/provider/prompt", "secret-from-prompt")


def test_prompt_and_set_rejects_prompt_cancellation(monkeypatch):
    store = WindowsCredentialStore()
    monkeypatch.setattr(
        "agent_bridge.config.credentials._prompt_windows_secret",
        lambda display_name: None,
    )

    try:
        store.prompt_and_set("agent-bridge/provider/prompt", "Prompt Name")
    except CredentialUnavailable as error:
        assert "cancel" in str(error).lower()
    else:
        raise AssertionError("cancelled prompt was accepted")
