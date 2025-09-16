import backend.domain.types as domain
from backend.validators import select_random_different_validator


def create_test_validator(
    address: str,
    provider: str,
    model: str,
    stake: int = 100,
    plugin: str = "openai",
    plugin_config: dict | None = None,
) -> domain.Validator:
    """Helper function to create test validators"""
    if plugin_config is None:
        plugin_config = {"api_key_env_var": "TEST_API_KEY", "api_url": "test_url"}

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


class TestFallbackSelection:
    """Test cases for fallback provider selection logic"""

    def test_different_provider_priority_1(self):
        """Test Priority 1: Different provider class selection from existing validators"""
        # Setup: OpenAI and Anthropic validators
        openai_validator = create_test_validator("addr1", "openai", "gpt-4o")
        anthropic_validator = create_test_validator("addr2", "anthropic", "claude-3")

        validators = [openai_validator, anthropic_validator]

        # Test fallback for OpenAI should select Anthropic
        fallback = select_random_different_validator(openai_validator, validators)
        assert fallback == anthropic_validator

        # Test fallback for Anthropic should select OpenAI
        fallback = select_random_different_validator(anthropic_validator, validators)
        assert fallback == openai_validator

    def test_different_provider_multiple_options(self):
        """Test Priority 1 with multiple different provider options"""
        # Setup: OpenAI, Anthropic, and Google validators
        openai_validator = create_test_validator("addr1", "openai", "gpt-4o")
        anthropic_validator = create_test_validator("addr2", "anthropic", "claude-3")
        google_validator = create_test_validator("addr3", "google", "gemini-pro")

        validators = [openai_validator, anthropic_validator, google_validator]

        # Test fallback for OpenAI should select from {Anthropic, Google}
        fallback = select_random_different_validator(openai_validator, validators)
        assert fallback in [anthropic_validator, google_validator]
        assert fallback != openai_validator

    def test_same_provider_different_model_priority_2(self):
        """Test Priority 2: Same provider class, different model from existing validators"""
        # Setup: Multiple OpenAI validators with different models
        openai_gpt4o = create_test_validator("addr1", "openai", "gpt-4o")
        openai_gpt4mini = create_test_validator("addr2", "openai", "gpt-4-mini")
        openai_gpt35 = create_test_validator("addr3", "openai", "gpt-3.5-turbo")

        validators = [openai_gpt4o, openai_gpt4mini, openai_gpt35]

        # Test fallback for gpt-4o should select from other OpenAI models
        fallback = select_random_different_validator(openai_gpt4o, validators)
        assert fallback in [openai_gpt4mini, openai_gpt35]
        assert fallback != openai_gpt4o

    def test_priority_order_different_provider_wins(self):
        """Test that Priority 1 (different provider) takes precedence over Priority 2 (same provider, different model)"""
        # Setup: Multiple OpenAI models + one Anthropic
        openai_gpt4o = create_test_validator("addr1", "openai", "gpt-4o")
        openai_gpt4mini = create_test_validator("addr2", "openai", "gpt-4-mini")
        anthropic_validator = create_test_validator("addr3", "anthropic", "claude-3")

        validators = [openai_gpt4o, openai_gpt4mini, anthropic_validator]

        # Test that fallback for OpenAI should prefer Anthropic (Priority 1) over other OpenAI (Priority 2)
        fallback = select_random_different_validator(openai_gpt4o, validators)
        assert fallback == anthropic_validator
        assert (
            fallback != openai_gpt4mini
        )  # Should not select same provider even if different model

    def test_no_fallback_single_validator(self):
        """Test no fallback scenario: Single validator only"""
        single_validator = create_test_validator("addr1", "openai", "gpt-4o")
        validators = [single_validator]

        fallback = select_random_different_validator(single_validator, validators)
        assert fallback is None

    def test_no_fallback_same_provider_same_model(self):
        """Test no fallback scenario: Only validators of same provider with same model"""
        # Setup: Multiple validators with identical provider and model
        validator1 = create_test_validator("addr1", "openai", "gpt-4o")
        validator2 = create_test_validator("addr2", "openai", "gpt-4o")
        validator3 = create_test_validator("addr3", "openai", "gpt-4o")

        validators = [validator1, validator2, validator3]

        # No fallback should be found since all have same provider and model
        fallback = select_random_different_validator(validator1, validators)
        assert fallback is None

    def test_excludes_self_from_selection(self):
        """Test that validator doesn't select itself as fallback"""
        # Setup: Two identical validators (same provider, model) but different addresses
        validator1 = create_test_validator("addr1", "openai", "gpt-4o")
        validator2 = create_test_validator("addr2", "openai", "gpt-4o")

        validators = [validator1, validator2]

        # Should return None since the only other validator has same provider and model
        fallback = select_random_different_validator(validator1, validators)
        assert fallback is None

    def test_empty_validator_list(self):
        """Test edge case: Empty validator list"""
        validator = create_test_validator("addr1", "openai", "gpt-4o")
        validators = []

        fallback = select_random_different_validator(validator, validators)
        assert fallback is None

    def test_random_selection_consistency(self):
        """Test that random selection is deterministic within the same pool"""
        # Setup multiple validators of different providers
        openai_validator = create_test_validator("addr1", "openai", "gpt-4o")
        anthropic_validator1 = create_test_validator("addr2", "anthropic", "claude-3")
        anthropic_validator2 = create_test_validator("addr3", "anthropic", "claude-2")
        google_validator = create_test_validator("addr4", "google", "gemini-pro")

        validators = [
            openai_validator,
            anthropic_validator1,
            anthropic_validator2,
            google_validator,
        ]

        # All non-OpenAI validators should be potential fallbacks
        expected_fallbacks = [
            anthropic_validator1,
            anthropic_validator2,
            google_validator,
        ]

        fallback = select_random_different_validator(openai_validator, validators)
        assert fallback in expected_fallbacks

    def test_complex_mixed_scenario(self):
        """Test complex scenario with mixed provider types and models"""
        # Setup: Mix of different providers and same providers with different models
        openai_gpt4o = create_test_validator("addr1", "openai", "gpt-4o")
        openai_gpt4mini = create_test_validator("addr2", "openai", "gpt-4-mini")
        anthropic_claude3 = create_test_validator("addr3", "anthropic", "claude-3")
        anthropic_claude2 = create_test_validator("addr4", "anthropic", "claude-2")
        google_gemini = create_test_validator("addr5", "google", "gemini-pro")

        validators = [
            openai_gpt4o,
            openai_gpt4mini,
            anthropic_claude3,
            anthropic_claude2,
            google_gemini,
        ]

        # For OpenAI, should prefer different providers (Priority 1)
        fallback = select_random_different_validator(openai_gpt4o, validators)
        assert fallback in [anthropic_claude3, anthropic_claude2, google_gemini]
        assert fallback not in [openai_gpt4mini, openai_gpt4o]

        # For Anthropic, should prefer different providers (Priority 1)
        fallback = select_random_different_validator(anthropic_claude3, validators)
        assert fallback in [openai_gpt4o, openai_gpt4mini, google_gemini]
        assert fallback not in [anthropic_claude3, anthropic_claude2]

    def test_provider_attributes_preserved(self):
        """Test that selected fallback validator preserves all necessary attributes"""
        openai_validator = create_test_validator("addr1", "openai", "gpt-4o", stake=100)
        anthropic_validator = create_test_validator(
            "addr2", "anthropic", "claude-3", stake=200
        )

        validators = [openai_validator, anthropic_validator]

        fallback = select_random_different_validator(openai_validator, validators)

        assert fallback == anthropic_validator
        if fallback:
            assert fallback.address == "addr2"
            assert fallback.llmprovider.provider == "anthropic"
            assert fallback.llmprovider.model == "claude-3"
            assert fallback.stake == 200
            assert (
                fallback.llmprovider.plugin_config["api_key_env_var"] == "TEST_API_KEY"
            )
            assert fallback.llmprovider.plugin_config["api_url"] == "test_url"
