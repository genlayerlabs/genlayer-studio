__all__ = ("Manager", "with_lock")

import typing
import contextlib
import dataclasses
import os
import hashlib
import json

from copy import deepcopy
from pathlib import Path

from .llm import LLMModule, SimulatorProvider
from .web import WebModule
from .base import ChangedConfigFile

import backend.database_handler.validators_registry as vr
from sqlalchemy.orm import Session

import backend.domain.types as domain


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
    genvm_host_arg: typing.Any


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

        # Track current LLM configuration to avoid unnecessary reconfigurations
        self._current_llm_config_hash = None
        self._web_module_available = False

        self.registry = ModifiableValidatorsRegistryInterceptor(
            self, validators_registry_session
        )

        self.llm_module = LLMModule()
        self.web_module = WebModule()

        self._genvm_config = ChangedConfigFile("genvm.yaml")
        with self._genvm_config.change_default() as config:
            config["modules"]["llm"]["address"] = "ws://" + self.llm_module.address
            # Initially include web module address, will be updated if it fails
            config["modules"]["web"]["address"] = "ws://" + self.web_module.address

        self._genvm_config.write_default()

    async def restart(self):
        await self.lock.writer.acquire()
        try:
            # Restart LLM module (required)
            await self.llm_module.restart()
            
            # Try to restart web module, but don't fail if it doesn't work
            try:
                await self.web_module.restart()
                self._web_module_available = True
                print("[ValidatorManager] Web module started successfully")
            except Exception as e:
                self._web_module_available = False
                print(f"[ValidatorManager] Web module failed to start: {e}")
                print("[ValidatorManager] Continuing without web module support")

            # Initialize LLM configuration from database
            snapshot = await self._get_snap_from_registry()
            await self._update_llm_config_if_needed(snapshot)
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
            raise Exception("service was not terminated")

    def _compute_llm_config_hash(self, validators: list[domain.Validator]) -> str:
        """Compute a hash of the LLM configuration to detect changes."""
        config_data = []
        for val in validators:
            config_data.append({
                "address": val.address,
                "model": val.llmprovider.model,
                "plugin": val.llmprovider.plugin,
                "url": val.llmprovider.plugin_config.get("api_url"),
                "key_env": val.llmprovider.plugin_config.get("api_key_env_var"),
            })
        config_json = json.dumps(config_data, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()

    async def _get_snap_from_registry(self) -> Snapshot:
        cur_validators_as_dict = self.registry.get_all_validators()
        print(f"[ValidatorManager] Retrieved {len(cur_validators_as_dict)} validators from registry")
        validators = [domain.Validator.from_dict(i) for i in cur_validators_as_dict]
        snapshot = await self._get_snap_from_validators(validators)
        print(f"[ValidatorManager] Created snapshot with {len(snapshot.nodes)} validator nodes")
        return snapshot

    async def _get_snap_from_validators(
        self, validators: list[domain.Validator]
    ) -> Snapshot:
        current_validators: list[SingleValidatorSnapshot] = []
        for val in validators:
            host_data = {"studio_llm_id": f"node-{val.address}"}
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
            current_validators.append(SingleValidatorSnapshot(val, host_data))
        return Snapshot(
            nodes=current_validators, genvm_config_path=self._genvm_config.new_path
        )

    @contextlib.asynccontextmanager
    async def snapshot(self):
        await self.lock.reader.acquire()
        try:
            # Verify LLM module is ready
            await self.llm_module.verify_for_read()
            
            # Only verify web module if it's available
            if self._web_module_available:
                try:
                    await self.web_module.verify_for_read()
                except Exception as e:
                    print(f"[ValidatorManager] Web module verification failed: {e}")
                    self._web_module_available = False

            # Always fetch fresh data from database
            snap = await self._get_snap_from_registry()
            
            # Ensure LLM configuration is current
            await self._update_llm_config_if_needed(snap)
            
            yield snap
        finally:
            self.lock.reader.release()

    @contextlib.asynccontextmanager
    async def temporal_snapshot(self, validators: list[domain.Validator]):
        await self.lock.writer.acquire()
        try:
            # Verify LLM module is ready
            await self.llm_module.verify_for_read()
            
            # Only verify web module if it's available
            if self._web_module_available:
                try:
                    await self.web_module.verify_for_read()
                except Exception as e:
                    print(f"[ValidatorManager] Web module verification failed: {e}")
                    self._web_module_available = False

            # Save current configuration hash
            original_hash = self._current_llm_config_hash

            # Create temporary snapshot and update LLM config
            temp_snapshot = await self._get_snap_from_validators(validators)
            await self._update_llm_config_if_needed(temp_snapshot)

            try:
                yield temp_snapshot
            finally:
                # Restore original configuration if it changed
                if original_hash != self._current_llm_config_hash:
                    original_snapshot = await self._get_snap_from_registry()
                    await self._update_llm_config_if_needed(original_snapshot)
        finally:
            self.lock.writer.release()

    async def _update_llm_config_if_needed(self, snap: Snapshot):
        """Update LLM module configuration only if validators have changed."""
        # Extract validator list from snapshot
        validators = [node.validator for node in snap.nodes]
        
        # Compute hash of current configuration
        new_hash = self._compute_llm_config_hash(validators)
        
        # Only update if configuration has changed
        if new_hash != self._current_llm_config_hash:
            print(f"[ValidatorManager] LLM configuration changed, updating...")
            
            new_providers: list[SimulatorProvider] = []
            
            for i in snap.nodes:
                new_providers.append(
                    SimulatorProvider(
                        model=i.validator.llmprovider.model,
                        id=f"node-{i.validator.address}",
                        url=i.validator.llmprovider.plugin_config["api_url"],
                        plugin=i.validator.llmprovider.plugin,
                        key_env=i.validator.llmprovider.plugin_config["api_key_env_var"],
                    )
                )
            
            await self.llm_module.change_config(new_providers)
            self._current_llm_config_hash = new_hash
            print(f"[ValidatorManager] LLM configuration updated with {len(new_providers)} providers")
        else:
            print(f"[ValidatorManager] LLM configuration unchanged, skipping update")

    @contextlib.asynccontextmanager
    async def do_write(self):
        await self.lock.writer.acquire()
        try:
            yield
            
            # After write operation, update LLM configuration if needed
            snapshot = await self._get_snap_from_registry()
            await self._update_llm_config_if_needed(snapshot)
        finally:
            self.lock.writer.release()
