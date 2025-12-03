import json
from dataclasses import dataclass
from sqlalchemy.orm import Session
import backend.validators as validators
from loguru import logger


@dataclass
class ValidatorConfig:
    stake: int
    provider: str
    model: str
    config: dict | None = None
    plugin: str | None = None
    plugin_config: dict | None = None
    amount: int = 1


async def initialize_validators(
    validators_json: str,
    db_session: Session,
    validators_manager: validators.Manager,
):
    """
    Idempotently initialize validators from a JSON string by deleting all existing validators and creating new ones.

    Args:
        validators_json: JSON string containing validator configurations
        db_session: Session to store validator information
        validators_manager: ValidatorsManager instance (required for snapshot updates)
    """

    if not validators_json:
        print("No validators to initialize")
        return

    try:
        validators_data = json.loads(validators_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in validators_json: {str(e)}")

    if not isinstance(validators_data, list):
        raise ValueError("validators_json must contain a JSON array")

    # Delete all existing validators using the manager's registry (triggers snapshot update)
    await validators_manager.registry.delete_all_validators()

    # Import necessary dependencies for validator creation
    from backend.database_handler.accounts_manager import AccountsManager
    from backend.domain.types import Validator, LLMProvider
    from backend.node.create_nodes.providers import get_default_provider_for

    accounts_manager = AccountsManager(db_session)

    # Create new validators
    for validator_data in validators_data:
        try:
            validator_config = ValidatorConfig(**validator_data)

            for _ in range(validator_config.amount):
                # Prepare LLM provider
                llm_provider = get_default_provider_for(
                    validator_config.provider, validator_config.model
                )
                if validator_config.config is not None:
                    llm_provider.config = validator_config.config
                if validator_config.plugin is not None:
                    llm_provider.plugin = validator_config.plugin
                if validator_config.plugin_config is not None:
                    llm_provider.plugin_config = validator_config.plugin_config

                # Create account
                account = accounts_manager.create_new_account()

                # Create validator using manager's registry (triggers snapshot update)
                await validators_manager.registry.create_validator(
                    Validator(
                        address=account.address,
                        private_key=account.key,
                        stake=validator_config.stake,
                        llmprovider=llm_provider,
                    )
                )

        except Exception as e:
            raise ValueError(f"Failed to create validator `{validator_data}`: {str(e)}")
