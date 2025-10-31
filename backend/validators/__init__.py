__all__ = ("Manager", "with_lock", "select_random_different_validator")

import typing
import contextlib
import dataclasses
import logging
import os
import random

from copy import deepcopy
from pathlib import Path

from .llm import LLMModule, SimulatorProvider
from .web import WebModule
import backend.validators.base as base

import backend.database_handler.validators_registry as vr
from sqlalchemy.orm import Session

import backend.domain.types as domain


def select_random_different_validator(
    primary_validator: domain.Validator, all_validators: list[domain.Validator]
) -> domain.Validator | None:
    """
    Select a random validator for fallback with two-tier priority system.

    Priority 1: Different provider class from existing validators
    Priority 2: Same provider class, different model from existing validators

    Args:
        primary_validator: The current validator
        all_validators: List of all existing validators (user-configured)

    Returns:
        Fallback validator object, or None if no suitable fallback
    """
    primary_provider_class = primary_validator.llmprovider.provider
    primary_model = primary_validator.llmprovider.model

    # Priority 1: Different provider classes from existing validators
    different_provider_validators = [
        v
        for v in all_validators
        if (
            v.llmprovider.provider != primary_provider_class
            and v.address != primary_validator.address
        )
    ]

    if different_provider_validators:
        return random.choice(different_provider_validators)

    # Priority 2: Same provider class, different model from existing validators
    same_provider_different_model = [
        v
        for v in all_validators
        if (
            v.llmprovider.provider == primary_provider_class
            and v.llmprovider.model != primary_model
            and v.address != primary_validator.address
        )
    ]

    if same_provider_different_model:
        return random.choice(same_provider_different_model)

    # No suitable fallback found
    return None


logger = logging.getLogger(__name__)


class ILock(typing.Protocol):
    async def acquire(self) -> None: ...
    def release(self) -> None: ...


@contextlib.asynccontextmanager
async def with_lock(lock: ILock):
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


class ModifiableValidatorsRegistryInterceptor(vr.ModifiableValidatorsRegistry):
    def __init__(self, parent: "Manager", *args, **kwargs):
        self._parent = parent
        super().__init__(*args, **kwargs)

    async def create_validator(self, validator: vr.Validator) -> dict:
        async with self._parent.do_write():
            res = await super().create_validator(validator)
            self.session.commit()
            return res

    async def update_validator(
        self,
        new_validator: vr.Validator,
    ) -> dict:
        async with self._parent.do_write():
            res = await super().update_validator(new_validator)
            self.session.commit()
            return res

    async def delete_validator(self, validator_address):
        async with self._parent.do_write():
            res = await super().delete_validator(validator_address)
            self.session.commit()
            return res

    async def delete_all_validators(self):
        async with self._parent.do_write():
            res = await super().delete_all_validators()
            self.session.commit()
            return res


@dataclasses.dataclass
class SingleValidatorSnapshot:
    validator: domain.Validator
    genvm_host_data: typing.Any


@dataclasses.dataclass
class Snapshot:
    nodes: list[SingleValidatorSnapshot]

    genvm_config_path: Path


class Manager:
    registry: vr.ModifiableValidatorsRegistry

    def __init__(self, validators_registry_session: Session):
        self._terminated = False
        from aiorwlock import RWLock

        self.lock = RWLock()

        self._cached_snapshot = None

        self.registry = ModifiableValidatorsRegistryInterceptor(
            self, validators_registry_session
        )

        self.llm_module = LLMModule()
        self.web_module = WebModule()

        self._genvm_config = base.ChangedConfigFile(base.GENVM_CONFIG_PATH)
        with self._genvm_config.change_default() as config:
            config["modules"]["llm"]["address"] = "ws://" + self.llm_module.address
            config["modules"]["web"]["address"] = "ws://" + self.web_module.address

        self._genvm_config.write_default()

    async def restart(self):
        await self.lock.writer.acquire()
        try:
            # Restart both LLM and web modules to ensure clean state
            await self.llm_module.restart()
            await self.web_module.restart()

            # Fetches the validators from the database
            # creates the general Snapshot with:
            # - SingleValidatorSnapshot (validator, genvm_host_data)
            # - the genvm_config_path
            new_validators = await self._get_snap_from_registry()
            # Registers all the validators providers and models to the LLM module
            await self._change_providers_from_snapshot(new_validators)
        finally:
            self.lock.writer.release()

    async def terminate(self):
        if self._terminated:
            return
        self._terminated = True

        await self.lock.writer.acquire()
        try:
            await self.llm_module.terminate()
            await self.web_module.terminate()

            self._genvm_config.terminate()
        finally:
            self.lock.writer.release()

    def __del__(self):
        if not self._terminated:
            logger.error(
                "ValidatorsManager was garbage collected without being terminated properly. "
                "This may indicate a reference leak or improper cleanup."
            )

    async def _get_snap_from_registry(self) -> Snapshot:
        cur_validators_as_dict = self.registry.get_all_validators()
        logger.info(
            "ValidatorManager retrieved %d validators from registry",
            len(cur_validators_as_dict),
        )
        validators = [domain.Validator.from_dict(i) for i in cur_validators_as_dict]
        snapshot = await self._get_snap_from_validators(validators)
        logger.info(
            "ValidatorManager created snapshot with %d validator nodes",
            len(snapshot.nodes),
        )
        return snapshot

    async def _get_snap_from_validators(
        self, validators: list[domain.Validator]
    ) -> Snapshot:
        current_validators: list[SingleValidatorSnapshot] = []
        has_multiple_validators = len(validators) > 1

        for val in validators:
            host_data = {
                "studio_llm_id": f"node-{val.address}",
                "node_address": val.address,
            }
            if (
                "mock_response" in val.llmprovider.plugin_config
                and len(val.llmprovider.plugin_config["mock_response"]) > 0
            ):
                host_data["mock_response"] = val.llmprovider.plugin_config[
                    "mock_response"
                ]
            if val.llmprovider.plugin == "custom":
                plugin_config = {
                    str(k): str(v) for k, v in val.llmprovider.plugin_config.items()
                }
                plugin_config["api_key_env_var"] = str(
                    os.getenv(val.llmprovider.plugin_config["api_key_env_var"])
                )

                host_data["custom_plugin_data"] = {
                    "model": str(val.llmprovider.model),
                    "config": {
                        str(k): str(v) if isinstance(v, (int, float, bool)) else v
                        for k, v in val.llmprovider.config.items()
                    },
                    "plugin_config": plugin_config,
                }
                val.llmprovider.plugin = (
                    "openai-compatible"  # so genvm thinks it is an implemented plugin
                )
            if has_multiple_validators:
                fallback_validator = select_random_different_validator(val, validators)
                if fallback_validator:
                    host_data["fallback_llm_id"] = f"node-{fallback_validator.address}"
                    val.fallback_validator = fallback_validator.address

            current_validators.append(SingleValidatorSnapshot(val, host_data))
        return Snapshot(
            nodes=current_validators, genvm_config_path=self._genvm_config.new_path
        )

    @contextlib.asynccontextmanager
    async def snapshot(self):
        await self.lock.reader.acquire()
        try:
            await self.llm_module.verify_for_read()
            await self.web_module.verify_for_read()

            assert self._cached_snapshot is not None

            snap = deepcopy(self._cached_snapshot)
            yield snap
        finally:
            self.lock.reader.release()

    @contextlib.asynccontextmanager
    async def temporal_snapshot(self, validators: list[domain.Validator]):
        await self.lock.writer.acquire()
        try:
            await self.llm_module.verify_for_read()
            await self.web_module.verify_for_read()

            original_snapshot = deepcopy(self._cached_snapshot)

            temp_snapshot = await self._get_snap_from_validators(validators)
            await self._change_providers_from_snapshot(temp_snapshot)

            try:
                yield deepcopy(temp_snapshot)
            finally:
                if original_snapshot is not None:
                    await self._change_providers_from_snapshot(original_snapshot)
        finally:
            self.lock.writer.release()

    async def _change_providers_from_snapshot(self, snap: Snapshot):
        self._cached_snapshot = None

        new_providers: list[SimulatorProvider] = []

        all_validators = [node.validator for node in snap.nodes]
        has_multiple_validators = len(all_validators) > 1

        for i in snap.nodes:
            new_providers.append(
                SimulatorProvider(
                    id=f"node-{i.validator.address}",
                    model=i.validator.llmprovider.model,
                    url=i.validator.llmprovider.plugin_config["api_url"],
                    plugin=i.validator.llmprovider.plugin,
                    key_env=i.validator.llmprovider.plugin_config["api_key_env_var"],
                )
            )

        await self.llm_module.change_config(new_providers)

        self._cached_snapshot = snap

    @contextlib.asynccontextmanager
    async def do_write(self):
        await self.lock.writer.acquire()
        try:
            yield

            new_validators = await self._get_snap_from_registry()
            await self._change_providers_from_snapshot(new_validators)
        finally:
            self.lock.writer.release()
