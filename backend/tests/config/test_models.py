from datetime import UTC, datetime

from agent_bridge.config.models import (
    ConnectionCapabilities,
    ConnectionTestStatus,
    ProviderProfile,
    normalize_base_url,
)


def test_profile_public_dict_has_no_secret_field():
    profile = ProviderProfile.new(
        display_name="ChatGPT 中转站",
        protocol="openai_compatible",
        base_url="https://relay.example/v1/",
        model_name="gpt-test",
    )
    public = profile.public_dict()
    assert public["base_url"] == "https://relay.example/v1"
    assert public["credential_status"] == "UNCONFIGURED"
    assert "api_key" not in public
    assert "secret" not in public


def test_normalize_base_url_rejects_non_http_and_duplicate_slash():
    assert normalize_base_url("https://relay.example/v1/") == "https://relay.example/v1"
    try:
        normalize_base_url("file:///tmp/provider")
    except ValueError as error:
        assert "http" in str(error)
    else:
        raise AssertionError("non-http URL was accepted")


def test_connection_capabilities_and_status_are_serializable():
    capabilities = ConnectionCapabilities(model_list=True, streaming=False)
    profile = ProviderProfile.new(
        display_name="relay",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:8000",
        model_name=None,
    )
    updated = profile.with_test(
        status=ConnectionTestStatus.PASSED,
        capabilities=capabilities,
        error=None,
    )
    assert updated.last_test_status is ConnectionTestStatus.PASSED
    assert updated.public_dict()["capabilities"]["streaming"] is False
