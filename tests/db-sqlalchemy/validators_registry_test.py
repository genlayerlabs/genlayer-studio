from datetime import datetime
from typing import Iterable
from eth_account import Account

import pytest
from sqlalchemy.orm import Session

from backend.database_handler.validators_registry import ModifiableValidatorsRegistry
from backend.domain.types import LLMProvider, Validator


@pytest.fixture
def validators_registry(session: Session) -> Iterable[ModifiableValidatorsRegistry]:
    yield ModifiableValidatorsRegistry(session)


@pytest.mark.asyncio
async def test_validators_registry(validators_registry: ModifiableValidatorsRegistry):
    stake = 1
    provider = "ollama"
    plugin = "ollama"
    model = "llama3"
    config = {}
    plugin_config = {}
    llm_provider = LLMProvider(
        provider=provider,
        model=model,
        config=config,
        plugin=plugin,
        plugin_config=plugin_config,
    )
    validator_account = Account.create()
    validator = Validator(
        address=validator_account.address,
        private_key=validator_account.key,
        stake=stake,
        llmprovider=llm_provider,
    )
    validator_address = validator.address

    # Create
    actual_validator = await validators_registry.create_validator(validator)
    assert validators_registry.count_validators() == 1

    assert actual_validator["stake"] == stake
    assert actual_validator["provider"] == provider
    assert actual_validator["model"] == model
    assert actual_validator["config"] == config
    created_at = actual_validator["created_at"]
    validator_id = actual_validator["id"]
    assert datetime.fromisoformat(created_at)

    actual_validators = validators_registry.get_all_validators()

    actual_validator = validators_registry.get_validator(validator_address)

    assert actual_validators == [actual_validator]

    # Update
    new_stake = 2
    new_provider = "ollama_new"
    new_model = "llama3.1"
    new_config = {"seed": 1, "key": {"array": [1, 2, 3]}}

    validator.stake = new_stake
    validator.llmprovider.provider = new_provider
    validator.llmprovider.model = new_model
    validator.llmprovider.config = new_config

    actual_validator = await validators_registry.update_validator(validator)

    assert validators_registry.count_validators() == 1

    assert (
        validators_registry.get_validator(validator_address, False) == actual_validator
    )

    assert actual_validator["stake"] == new_stake
    assert actual_validator["provider"] == new_provider
    assert actual_validator["model"] == new_model
    assert actual_validator["config"] == new_config
    assert actual_validator["id"] == validator_id
    assert actual_validator["created_at"] == created_at

    # Delete
    await validators_registry.delete_validator(validator_address)

    assert len(validators_registry.get_all_validators()) == 0
    assert validators_registry.count_validators() == 0
