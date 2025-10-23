# consensus/domain/state.py

from typing import List
from sqlalchemy.orm import Session

from backend.domain.types import Validator

from .models import Validators
from backend.errors.errors import ValidatorNotFound


# the to_dict function lives in this module and not in models.py because it's on this layer of abstraction where we convert database objects to our custom data structures
def to_dict(validator: Validators, include_private_key: bool = True) -> dict:
    return {
        "id": validator.id,
        "address": validator.address,
        "stake": validator.stake,
        "provider": validator.provider,
        "model": validator.model,
        "config": validator.config,
        "plugin": validator.plugin,
        "plugin_config": validator.plugin_config,
        "created_at": validator.created_at.isoformat(),
        **({"private_key": validator.private_key} if include_private_key else {}),
    }


class ValidatorsRegistry:
    def __init__(self, session: Session):
        self.session = session
        self.db_validators_table = "validators"

    def _get_validator_or_fail(self, validator_address: str) -> Validators:
        """Private method to check if an account exists, and raise an error if not."""

        validator_data = (
            self.session.query(Validators)
            .filter(Validators.address == validator_address)
            .one_or_none()
        )

        if validator_data is None:
            raise ValidatorNotFound(validator_address)
        return validator_data

    def count_validators(self) -> int:
        # Expire all objects to ensure we get fresh data from the database
        self.session.expire_all()
        return self.session.query(Validators).count()

    def get_all_validators(self, include_private_key: bool = True) -> List[dict]:
        # Expire all objects to ensure we get fresh data from the database
        self.session.expire_all()
        validators_data = self.session.query(Validators).all()
        return [
            to_dict(validator, include_private_key) for validator in validators_data
        ]

    def get_validator(
        self, validator_address: str, include_private_key: bool = True
    ) -> dict:
        # Expire all objects to ensure we get fresh data from the database
        self.session.expire_all()
        return to_dict(
            self._get_validator_or_fail(validator_address), include_private_key
        )


class ModifiableValidatorsRegistry(ValidatorsRegistry):
    async def create_validator(self, validator: Validator) -> dict:
        db_validator = _to_db_model(validator)
        self.session.add(db_validator)
        self.session.flush()  # Ensure the validator is persisted and queryable
        return self.get_validator(validator.address, False)

    async def update_validator(
        self,
        new_validator: Validator,
    ) -> dict:
        validator = self._get_validator_or_fail(new_validator.address)

        validator.stake = new_validator.stake
        validator.provider = new_validator.llmprovider.provider
        validator.model = new_validator.llmprovider.model
        validator.config = new_validator.llmprovider.config
        validator.plugin = new_validator.llmprovider.plugin
        validator.plugin_config = new_validator.llmprovider.plugin_config

        self.session.flush()  # Ensure the validator update is persisted
        return to_dict(validator, False)

    async def delete_validator(self, validator_address):
        validator = self._get_validator_or_fail(validator_address)

        self.session.delete(validator)
        self.session.flush()  # Ensure the validator deletion is persisted

    async def delete_all_validators(self):
        self.session.query(Validators).delete(synchronize_session=False)
        self.session.flush()  # Ensure all validator deletions are persisted


def _to_db_model(validator: Validator) -> Validators:
    return Validators(
        address=validator.address,
        stake=validator.stake,
        provider=validator.llmprovider.provider,
        model=validator.llmprovider.model,
        config=validator.llmprovider.config,
        plugin=validator.llmprovider.plugin,
        plugin_config=validator.llmprovider.plugin_config,
        private_key=validator.private_key.to_0x_hex(),
    )
