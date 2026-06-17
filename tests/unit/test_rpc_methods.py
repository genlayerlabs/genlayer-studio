from unittest.mock import AsyncMock

import pytest

from backend.protocol_rpc import rpc_methods


@pytest.mark.asyncio
async def test_update_validator_forwards_config_argument(monkeypatch):
    session = object()
    validators_manager = object()
    update_validator = AsyncMock(return_value={"address": "0xabc"})
    monkeypatch.setattr(rpc_methods.impl, "update_validator", update_validator)

    config = {"max_tokens": 500, "temperature": 0.75}
    plugin_config = {
        "api_key_env_var": "OPENROUTERAPIKEY",
        "api_url": "https://openrouter.ai/api",
    }

    result = await rpc_methods.update_validator(
        "0xabc",
        11,
        "openrouter",
        "@preset/rally-testnet-gpt-5-1",
        config,
        "openai-compatible",
        plugin_config,
        session=session,
        validators_manager=validators_manager,
    )

    assert result == {"address": "0xabc"}
    update_validator.assert_awaited_once_with(
        session=session,
        validators_manager=validators_manager,
        validator_address="0xabc",
        stake=11,
        provider="openrouter",
        model="@preset/rally-testnet-gpt-5-1",
        config=config,
        plugin="openai-compatible",
        plugin_config=plugin_config,
    )
