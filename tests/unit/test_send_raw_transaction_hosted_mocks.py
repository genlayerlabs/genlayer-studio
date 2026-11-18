"""DXP-666: hosted studio must reject transactions that use mocked validators."""

import os
from unittest.mock import MagicMock, patch

import pytest

from backend.protocol_rpc.endpoints import (
    _sim_config_has_mocked_validators,
    send_raw_transaction,
)
from backend.protocol_rpc.exceptions import JSONRPCError


def _validator(plugin_config):
    return {
        "stake": 1,
        "provider": "openai",
        "model": "gpt-4o",
        "config": {},
        "plugin": "openai-compatible",
        "plugin_config": plugin_config,
    }


MOCKED_LLM = {"validators": [_validator({"mock_response": {"response": {"a": "b"}}})]}
MOCKED_WEB = {
    "validators": [
        _validator({"mock_web_response": {"nondet_web_request": {"https://x": {}}}})
    ]
}
UNMOCKED = {
    "validators": [
        _validator({"api_key_env_var": "OPENAIKEY", "api_url": None}),
        _validator({"mock_response": {}, "mock_web_response": {}}),
    ]
}


@pytest.mark.parametrize(
    "sim_config,expected",
    [
        (None, False),
        ({}, False),
        ({"genvm_datetime": "2024-01-01T00:00:00Z"}, False),
        ({"validators": "bad"}, False),
        ({"validators": [{"plugin_config": None}]}, False),
        (UNMOCKED, False),
        (MOCKED_LLM, True),
        (MOCKED_WEB, True),
        (
            {"validators": [UNMOCKED["validators"][0], MOCKED_LLM["validators"][0]]},
            True,
        ),
    ],
)
def test_sim_config_has_mocked_validators(sim_config, expected):
    assert _sim_config_has_mocked_validators(sim_config) is expected


def _call(sim_config):
    return send_raw_transaction(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        "0xdeadbeef",
        sim_config,
    )


@pytest.mark.parametrize("sim_config", [MOCKED_LLM, MOCKED_WEB])
def test_hosted_rejects_mocked_validators(sim_config):
    with patch.dict(os.environ, {"VITE_IS_HOSTED": "true"}), patch(
        "backend.protocol_rpc.endpoints.AccountsManager"
    ) as accounts_manager:
        with pytest.raises(JSONRPCError) as exc_info:
            _call(sim_config)
    assert exc_info.value.code == -32000
    assert "Mocked validators are not allowed" in exc_info.value.message
    # Rejected before any DB work happens.
    accounts_manager.assert_not_called()


class _Proceeded(Exception):
    pass


@pytest.mark.parametrize(
    "env,sim_config",
    [
        ({"VITE_IS_HOSTED": "false"}, MOCKED_LLM),
        ({"VITE_IS_HOSTED": "true"}, UNMOCKED),
        ({"VITE_IS_HOSTED": "true"}, None),
    ],
)
def test_allowed_cases_proceed(env, sim_config):
    with patch.dict(os.environ, env), patch(
        "backend.protocol_rpc.endpoints.AccountsManager", side_effect=_Proceeded
    ):
        with pytest.raises(_Proceeded):
            _call(sim_config)
