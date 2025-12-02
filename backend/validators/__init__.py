__all__ = ("Manager", "with_lock", "select_random_different_validator")

import asyncio
import typing
import contextlib
import dataclasses
import logging
import os
import random

from copy import deepcopy
from pathlib import Path

import backend.database_handler.validators_registry as vr
from sqlalchemy.orm import Session

import backend.domain.types as domain


@dataclasses.dataclass
class SimulatorProvider:
    id: str
    model: str
    url: str
    plugin: str
    key_env: str


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


class ModifiableValidatorsRegistryInterceptor(vr.ModifiableValidatorsRegistry):
    def __init__(self, parent: "Manager", *args, **kwargs):
        self._parent = parent
        super().__init__(*args, **kwargs)

    async def create_validator(self, validator: vr.Validator) -> dict:
        async with self._parent.do_write():
            res = await super().create_validator(validator)
            self.session.commit()
            await self._parent._notify_validator_change("validator_created", res)
            return res

    async def update_validator(
        self,
        new_validator: vr.Validator,
    ) -> dict:
        async with self._parent.do_write():
            res = await super().update_validator(new_validator)
            self.session.commit()
            await self._parent._notify_validator_change("validator_updated", res)
            return res

    async def delete_validator(self, validator_address):
        async with self._parent.do_write():
            res = await super().delete_validator(validator_address)
            self.session.commit()
            await self._parent._notify_validator_change(
                "validator_deleted", {"address": validator_address}
            )
            return res

    async def delete_all_validators(self):
        async with self._parent.do_write():
            res = await super().delete_all_validators()
            self.session.commit()
            await self._parent._notify_validator_change("all_validators_deleted", {})
            return res


@dataclasses.dataclass
class SingleValidatorSnapshot:
    validator: domain.Validator
    genvm_host_data: typing.Any


@dataclasses.dataclass
class Snapshot:
    nodes: list[SingleValidatorSnapshot]


from backend.node.base import LLMConfig, Manager as GenVMManager


class Manager:
    registry: vr.ModifiableValidatorsRegistry

    def __init__(
        self, validators_registry_session: Session, genvm_manager: GenVMManager
    ):
        self.genvm_manager = genvm_manager

        self._cached_snapshot = None

        self.registry = ModifiableValidatorsRegistryInterceptor(
            self, validators_registry_session
        )
        self._restart_llm_lock = asyncio.Lock()

    async def restart(self):
        # Fetches the validators from the database
        # creates the general Snapshot with:
        # - SingleValidatorSnapshot (validator, genvm_host_data)
        # - the genvm_config_path
        new_validators = await self._get_snap_from_registry()
        # Registers all the validators providers and models to the LLM module
        await self._change_providers_from_snapshot(new_validators)

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
            host_data: dict[str, typing.Any] = {
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
            if (
                "mock_web_response" in val.llmprovider.plugin_config
                and len(val.llmprovider.plugin_config["mock_web_response"]) > 0
            ):
                host_data["mock_web_response"] = val.llmprovider.plugin_config[
                    "mock_web_response"
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
            nodes=current_validators,
        )

    @contextlib.asynccontextmanager
    async def snapshot(self):
        assert self._cached_snapshot is not None

        snap = deepcopy(self._cached_snapshot)
        yield snap

    @contextlib.asynccontextmanager
    async def temporal_snapshot(self, validators: list[domain.Validator]):
        original_snapshot = deepcopy(self._cached_snapshot)

        temp_snapshot = await self._get_snap_from_validators(validators)
        await self._change_providers_from_snapshot(temp_snapshot)

        try:
            yield deepcopy(temp_snapshot)
        finally:
            if original_snapshot is not None:
                await self._change_providers_from_snapshot(original_snapshot)

    async def _change_providers_from_snapshot(self, snap: Snapshot):
        async with self._restart_llm_lock:
            await self._change_providers_from_snapshot_locked(snap)

    async def _change_providers_from_snapshot_locked(self, snap: Snapshot):
        self._cached_snapshot = None

        new_providers: dict[str, LLMConfig] = {}

        all_validators = [node.validator for node in snap.nodes]
        has_multiple_validators = len(all_validators) > 1

        for i in snap.nodes:
            key_env = i.validator.llmprovider.plugin_config["api_key_env_var"]
            new_providers[f"node-{i.validator.address}"] = {
                "models": {
                    i.validator.llmprovider.model: {
                        "supports_json": True,
                        "meta": {
                            "config": i.validator.llmprovider.config,
                        },
                    }
                },
                "enabled": True,
                "host": i.validator.llmprovider.plugin_config["api_url"],
                "provider": i.validator.llmprovider.plugin,
                "key": f"${{ENV[{key_env}]}}",
            }

        await self.genvm_manager.stop_module("llm")
        new_llm_config = deepcopy(self.genvm_manager.llm_config_base)
        new_llm_config["backends"] = new_providers
        await self.genvm_manager.start_module(
            "llm", new_llm_config, {"allow_empty_backends": True}
        )

        self._cached_snapshot = snap

    @contextlib.asynccontextmanager
    async def do_write(self):
        yield

        new_validators = await self._get_snap_from_registry()
        await self._change_providers_from_snapshot(new_validators)

    async def _notify_validator_change(self, event_type: str, data: dict):
        """
        Notify other services about validator changes via Redis.
        This is only called by RPC service (not consensus-worker).
        """
        import json
        import os
        import redis.asyncio as aioredis

        # Get Redis URL from environment
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")

        try:
            # Create Redis client for publishing
            redis_client = aioredis.from_url(
                redis_url, encoding="utf-8", decode_responses=True
            )

            # Prepare message
            message = json.dumps(
                {
                    "event": event_type,
                    "data": data,
                }
            )

            # Publish to validator events channel for consensus-worker
            subscribers = await redis_client.publish("validator:events", message)

            # Close the client
            await redis_client.close()

            logger.info(
                f"Published validator change event: {event_type} to {subscribers} subscribers"
            )
        except Exception as e:
            logger.error(f"Failed to publish validator change event: {e}")
