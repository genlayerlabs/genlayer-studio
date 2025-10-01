import pytest
from unittest.mock import AsyncMock
from pathlib import Path
import types

import backend.domain.types as domain
from backend.validators import SingleValidatorSnapshot, Snapshot, Manager


def create_test_validator(
    address: str,
    provider: str,
    model: str,
    stake: int = 100,
    plugin: str | None = None,
    plugin_config: dict | None = None,
) -> domain.Validator:
    """Helper to create test validators"""
    if plugin is None:
        plugin = provider
    if plugin_config is None:
        plugin_config = {
            "api_key_env_var": f"{provider.upper()}_API_KEY",
            "api_url": f"{provider}_url",
        }

    llm_provider = domain.LLMProvider(
        provider=provider,
        model=model,
        config={"temperature": 0.75},
        plugin=plugin,
        plugin_config=plugin_config,
    )

    return domain.Validator(
        address=address,
        stake=stake,
        llmprovider=llm_provider,
    )


class TestValidatorsManagerHostData:
    """Test that Manager correctly sets host data and provider IDs by inspecting llm_module calls"""

    def create_mock_manager(self):
        """Create a minimal mock manager to test _change_providers_from_snapshot"""

        class MockManager:
            def __init__(self):
                self.llm_module = AsyncMock()
                self.llm_module.change_config = AsyncMock()
                self._cached_snapshot = None

        mock_manager = MockManager()
        actual_method = Manager._change_providers_from_snapshot
        mock_manager._change_providers_from_snapshot = types.MethodType(
            actual_method, mock_manager
        )
        return mock_manager

    @pytest.mark.asyncio
    async def test_single_validator_no_fallback_llm_config(self):
        """Test that single validator creates only one provider (no fallback)"""
        # Create mock manager
        mock_manager = self.create_mock_manager()

        # Create snapshot with single validator
        validator = create_test_validator("addr1", "openai", "gpt-4o")
        host_data = {"studio_llm_id": "node-addr1"}
        snapshot = Snapshot(
            nodes=[SingleValidatorSnapshot(validator, host_data)],
            genvm_config_path=Path("/mock/path"),
        )

        # Call the actual method
        await mock_manager._change_providers_from_snapshot(snapshot)

        # Verify no fallback_llm_id was added to host_data
        assert "fallback_llm_id" not in snapshot.nodes[0].genvm_host_data

        # Verify llm_module.change_config was called with only 1 provider
        mock_manager.llm_module.change_config.assert_called_once()
        providers = mock_manager.llm_module.change_config.call_args[0][0]

        assert len(providers) == 1
        assert providers[0].id == "node-addr1"

    @pytest.mark.asyncio
    async def test_multiple_validators_fallback_llm_config(self):
        """Test comprehensive provider data: models, URLs, plugins, API keys from llm_module.change_config"""
        # Create mock manager
        mock_manager = self.create_mock_manager()

        # Create validators with different providers, models, and configurations
        validator1 = create_test_validator(
            "test-addr-123",
            "openai",
            "gpt-4o",
            plugin_config={
                "api_key_env_var": "OPENAI_API_KEY",
                "api_url": "open_ai_url",
            },
        )
        validator2 = create_test_validator(
            "another-addr-456",
            "anthropic",
            "claude-3-5-sonnet",
            plugin_config={
                "api_key_env_var": "ANTHROPIC_API_KEY",
                "api_url": "anthropic_url",
            },
        )

        host_data1 = {"studio_llm_id": "node-test-addr-123"}
        host_data2 = {"studio_llm_id": "node-another-addr-456"}
        snapshot = Snapshot(
            nodes=[
                SingleValidatorSnapshot(validator1, host_data1),
                SingleValidatorSnapshot(validator2, host_data2),
            ],
            genvm_config_path=Path("/mock/path"),
        )

        # Call the actual method
        await mock_manager._change_providers_from_snapshot(snapshot)

        # Verify fallback_llm_id was added to host_data with correct format
        assert "fallback_llm_id" in snapshot.nodes[0].genvm_host_data
        assert "fallback_llm_id" in snapshot.nodes[1].genvm_host_data
        assert (
            snapshot.nodes[0].genvm_host_data["fallback_llm_id"]
            == "node-test-addr-123-1"
        )
        assert (
            snapshot.nodes[1].genvm_host_data["fallback_llm_id"]
            == "node-another-addr-456-1"
        )

        # Verify llm_module.change_config was called with 4 providers (2 primary + 2 fallback)
        mock_manager.llm_module.change_config.assert_called_once()
        providers = mock_manager.llm_module.change_config.call_args[0][0]

        assert len(providers) == 4

        # Organize providers by type for detailed validation
        primary_providers = {p.id: p for p in providers if not p.id.endswith("-1")}
        fallback_providers = {p.id: p for p in providers if p.id.endswith("-1")}

        assert len(primary_providers) == 2
        assert len(fallback_providers) == 2

        # Validate primary providers match original validator configurations
        openai_primary = primary_providers["node-test-addr-123"]
        anthropic_primary = primary_providers["node-another-addr-456"]

        # Primary provider for validator1 should have validator1's configuration
        assert openai_primary.model == "gpt-4o"
        assert openai_primary.url == "open_ai_url"
        assert openai_primary.plugin == "openai"
        assert openai_primary.key_env == "OPENAI_API_KEY"

        # Primary provider for validator2 should have validator2's configuration
        assert anthropic_primary.model == "claude-3-5-sonnet"
        assert anthropic_primary.url == "anthropic_url"
        assert anthropic_primary.plugin == "anthropic"
        assert anthropic_primary.key_env == "ANTHROPIC_API_KEY"

        # Validate fallback providers use the OTHER validator's configuration
        openai_fallback = fallback_providers["node-test-addr-123-1"]
        anthropic_fallback = fallback_providers["node-another-addr-456-1"]

        # OpenAI validator's fallback should use Anthropic's configuration (Priority 1: different provider)
        assert openai_fallback.model == "claude-3-5-sonnet"
        assert openai_fallback.url == "anthropic_url"
        assert openai_fallback.plugin == "anthropic"
        assert openai_fallback.key_env == "ANTHROPIC_API_KEY"

        # Anthropic validator's fallback should use OpenAI's configuration (Priority 1: different provider)
        assert anthropic_fallback.model == "gpt-4o"
        assert anthropic_fallback.url == "open_ai_url"
        assert anthropic_fallback.plugin == "openai"
        assert anthropic_fallback.key_env == "OPENAI_API_KEY"

        # Verify ID formats are correct
        assert "node-test-addr-123" in primary_providers
        assert "node-another-addr-456" in primary_providers
        assert "node-test-addr-123-1" in fallback_providers
        assert "node-another-addr-456-1" in fallback_providers

    @pytest.mark.asyncio
    async def test_identical_validators_no_fallback_llm_config(self):
        """Test that identical validators get no fallback providers"""
        # Create mock manager
        mock_manager = self.create_mock_manager()

        # Create identical validators
        validator1 = create_test_validator("addr1", "openai", "gpt-4o")
        validator2 = create_test_validator(
            "addr2", "openai", "gpt-4o"
        )  # Same provider and model

        host_data1 = {"studio_llm_id": "node-addr1"}
        host_data2 = {"studio_llm_id": "node-addr2"}
        snapshot = Snapshot(
            nodes=[
                SingleValidatorSnapshot(validator1, host_data1),
                SingleValidatorSnapshot(validator2, host_data2),
            ],
            genvm_config_path=Path("/mock/path"),
        )

        # Call the actual method
        await mock_manager._change_providers_from_snapshot(snapshot)

        # Verify no fallback_llm_id was added
        assert "fallback_llm_id" not in snapshot.nodes[0].genvm_host_data
        assert "fallback_llm_id" not in snapshot.nodes[1].genvm_host_data

        # Verify only 2 providers were created (no fallbacks)
        mock_manager.llm_module.change_config.assert_called_once()
        providers = mock_manager.llm_module.change_config.call_args[0][0]

        assert len(providers) == 2
        provider_ids = [p.id for p in providers]
        assert "node-addr1" in provider_ids
        assert "node-addr2" in provider_ids
        assert "node-addr1-1" not in provider_ids
        assert "node-addr2-1" not in provider_ids
