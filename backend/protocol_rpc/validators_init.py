import hashlib
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


def _current_config_hash(registry) -> str | None:
    """Derive a hash from the validators currently in the DB.

    Returns None if there are no validators.
    """
    all_validators = registry.get_all_validators(include_private_key=False)
    if not all_validators:
        return None
    # Build a comparable structure: list of (provider, model, stake, count) sorted
    from collections import Counter

    key_counts = Counter()
    for v in all_validators:
        provider = v.get("provider", "")
        model = v.get("model", "")
        stake = v.get("stake", 0)
        key_counts[(provider, model, stake)] += 1

    items = [
        {"provider": k[0], "model": k[1], "stake": k[2], "amount": c}
        for k, c in sorted(key_counts.items())
    ]
    normalized = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _desired_config_hash(validators_json: str) -> str:
    """Compute hash from the desired config in the same format as _current_config_hash."""
    from collections import Counter

    data = json.loads(validators_json)
    key_counts = Counter()
    for v in data:
        cfg = ValidatorConfig(**v)
        key_counts[(cfg.provider, cfg.model, cfg.stake)] += cfg.amount

    items = [
        {"provider": k[0], "model": k[1], "stake": k[2], "amount": c}
        for k, c in sorted(key_counts.items())
    ]
    normalized = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


async def initialize_validators(
    validators_json: str,
    db_session: Session,
    validators_manager: validators.Manager,
):
    """
    Initialize validators from a JSON config string.

    Compares the desired config against what's currently in the DB.
    Only performs the destructive delete-all/recreate cycle when the
    config has actually changed, preventing validator thrashing when
    multiple pods start with the same config (rollouts, restarts, canaries).
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

    # Compare desired config against current DB state
    try:
        desired_hash = _desired_config_hash(validators_json)
    except (TypeError, KeyError) as e:
        # Bad config fields — fall through to the creation loop which gives a better error
        desired_hash = None
    current_hash = _current_config_hash(validators_manager.registry)

    if desired_hash is not None and current_hash == desired_hash:
        logger.info(
            f"Validators config unchanged (hash={desired_hash}), skipping initialization"
        )
        return

    if current_hash is None:
        logger.info("No validators in DB, initializing from config")
    else:
        logger.info(
            f"Validators config changed (current={current_hash} desired={desired_hash}), reinitializing"
        )

    # Import necessary dependencies for validator creation
    from backend.database_handler.accounts_manager import AccountsManager
    from backend.domain.types import Validator, LLMProvider
    from backend.node.create_nodes.providers import get_default_provider_for

    accounts_manager = AccountsManager(db_session)

    # Collect all validators to create
    validators_to_create: list[Validator] = []

    for validator_data in validators_data:
        try:
            validator_config = ValidatorConfig(**validator_data)

            for _ in range(validator_config.amount):
                llm_provider = get_default_provider_for(
                    validator_config.provider, validator_config.model
                )
                if validator_config.config is not None:
                    llm_provider.config = validator_config.config
                if validator_config.plugin is not None:
                    llm_provider.plugin = validator_config.plugin
                if validator_config.plugin_config is not None:
                    llm_provider.plugin_config = validator_config.plugin_config

                account = accounts_manager.create_new_account()

                validators_to_create.append(
                    Validator(
                        address=account.address,
                        private_key=account.key,
                        stake=validator_config.stake,
                        llmprovider=llm_provider,
                    )
                )

        except Exception as e:
            raise ValueError(f"Failed to create validator `{validator_data}`: {str(e)}")

    # Atomic replace: delete all + create new in one transaction, one Redis event.
    # Workers never see 0 validators.
    if validators_to_create:
        logger.info(f"Atomic replacing validators ({len(validators_to_create)} new)")
        await validators_manager.registry.replace_all_validators(validators_to_create)
