from unittest.mock import AsyncMock, Mock

import pytest

from backend.validators import Manager, Snapshot, SingleValidatorSnapshot
from tests.unit.test_fallback_validator_model_host_data import create_test_validator


def _make_manager() -> Manager:
    genvm = Mock()
    genvm.stop_module = AsyncMock()
    genvm.start_module = AsyncMock()
    genvm.llm_config_base = {}
    manager = Manager(Mock(), genvm)
    return manager


def _registry_validator(address: str = "0xabc") -> dict:
    return {
        "address": address,
        "stake": 8,
        "provider": "openrouter",
        "model": "test-model",
        "config": {"temperature": 0.75},
        "plugin": "openai-compatible",
        "plugin_config": {
            "api_key_env_var": "OPENROUTERAPIKEY",
            "api_url": "https://openrouter.ai/api",
            "mock_response": {},
        },
    }


@pytest.mark.asyncio
async def test_snapshot_reloads_when_cache_empty_but_registry_has_validators():
    manager = _make_manager()
    manager._cached_snapshot = Snapshot(nodes=[])
    manager.registry.get_all_validators = Mock(return_value=[_registry_validator()])

    async with manager.snapshot() as snap:
        assert len(snap.nodes) == 1
        assert snap.nodes[0].validator.address == "0xabc"

    manager.genvm_manager.start_module.assert_awaited()


@pytest.mark.asyncio
async def test_snapshot_stays_empty_when_registry_empty():
    manager = _make_manager()
    manager._cached_snapshot = Snapshot(nodes=[])
    manager.registry.get_all_validators = Mock(return_value=[])

    async with manager.snapshot() as snap:
        assert snap.nodes == []

    manager.genvm_manager.start_module.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_uses_cached_nodes_without_refresh():
    manager = _make_manager()
    validator = create_test_validator("0xcached", "openai", "gpt-4o")
    manager._cached_snapshot = Snapshot(
        nodes=[SingleValidatorSnapshot(validator, {"studio_llm_id": "node-0xcached"})]
    )
    manager.registry.get_all_validators = Mock()

    async with manager.snapshot() as snap:
        assert len(snap.nodes) == 1
        assert snap.nodes[0].validator.address == "0xcached"

    manager.registry.get_all_validators.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_raises_if_not_initialized():
    manager = _make_manager()
    manager._cached_snapshot = None

    with pytest.raises(RuntimeError, match="not initialized"):
        async with manager.snapshot():
            pass
