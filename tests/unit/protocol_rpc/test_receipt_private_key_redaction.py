"""Receipts returned or raised by the call endpoints and the explorer API must
not expose the executing validator's private key (see the redaction added for
the transaction/block getters in #1731)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.domain.types import LLMProvider, Validator
from backend.node.genvm import base as genvmbase
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError
from backend.protocol_rpc.explorer import queries
from backend.validators import SingleValidatorSnapshot, Snapshot

SECRET = "0x" + "ab" * 32
CONTRACT = "0x8ba1f109551bD432803012645Ac136ddd64DBA72"
SENDER = "0x9F0e84243496AcFB3Cd99D02eA59673c05901501"


def _provider() -> LLMProvider:
    return LLMProvider(
        provider="openai",
        model="gpt-4o",
        config={},
        plugin="openai-compatible",
        plugin_config={
            "api_key_env_var": "OPENAIKEY",
            "api_url": "https://api.openai.com",
        },
    )


class _FakeContractSnapshot:
    contract_address = CONTRACT
    balance = 0
    states = {"accepted": {}, "finalized": {}}
    genvm_executor_selector = None

    def __init__(self, *_args, **_kwargs):
        pass


def _fake_run_genvm_host(result):
    async def run(host_supplier, **_kwargs):
        return genvmbase.ExecutionResult(
            result=result,
            eq_outputs={},
            pending_transactions=[],
            stdout="",
            stderr="",
            genvm_log=[],
            state=host_supplier.keywords["state_proxy"],
            processing_time=1,
            nondet_disagree=None,
        )

    return run


def _call_dependencies(monkeypatch, result):
    monkeypatch.delenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", raising=False)
    monkeypatch.setattr(genvmbase, "run_genvm_host", _fake_run_genvm_host(result))
    monkeypatch.setattr(endpoints, "ContractSnapshot", _FakeContractSnapshot)
    monkeypatch.setattr(endpoints, "_check_rate_limit", lambda _address: None)

    validator = Validator(
        address=SENDER, stake=1, llmprovider=_provider(), private_key=SECRET
    )
    snapshot = Snapshot(
        nodes=[SingleValidatorSnapshot(validator, {"node_address": SENDER})]
    )

    @asynccontextmanager
    async def _snapshot():
        yield snapshot

    validators_manager = MagicMock()
    validators_manager.snapshot = _snapshot

    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True

    transactions_parser = MagicMock()
    transactions_parser.decode_method_call_data.return_value = MagicMock(
        calldata=b"\x00"
    )

    msg_handler = MagicMock()
    msg_handler.with_client_session.return_value = MagicMock(client_session_id=None)

    return dict(
        session=MagicMock(),
        accounts_manager=accounts_manager,
        msg_handler=msg_handler,
        transactions_parser=transactions_parser,
        validators_manager=validators_manager,
        genvm_manager=MagicMock(url="http://127.0.0.1:1"),
    )


@pytest.mark.asyncio
async def test_sim_call_result_redacts_validator_private_key(monkeypatch):
    deps = _call_dependencies(monkeypatch, genvmbase.ExecutionReturn(ret=b"\x00"))

    result = await endpoints.sim_call(
        **deps,
        params={"type": "read", "data": "0x00", "to": CONTRACT, "from": SENDER},
    )

    assert result["node_config"]["address"] == SENDER
    assert "private_key" not in result["node_config"]


@pytest.mark.asyncio
async def test_gen_call_execution_failed_error_redacts_validator_private_key(
    monkeypatch,
):
    deps = _call_dependencies(
        monkeypatch,
        genvmbase.ExecutionError(message="boom", kind=genvmbase.ResultCode.USER_ERROR),
    )

    with pytest.raises(JSONRPCError) as exc:
        await endpoints.gen_call(
            **deps,
            params={"type": "read", "data": "0x00", "to": CONTRACT, "from": SENDER},
        )

    receipt = exc.value.to_dict()["data"]["receipt"]
    assert receipt["node_config"]["address"] == SENDER
    assert "private_key" not in receipt["node_config"]


@pytest.mark.asyncio
async def test_eth_call_execution_failed_error_redacts_validator_private_key(
    monkeypatch,
):
    deps = _call_dependencies(
        monkeypatch,
        genvmbase.ExecutionError(message="boom", kind=genvmbase.ResultCode.USER_ERROR),
    )
    monkeypatch.setattr(endpoints, "handle_consensus_data_call", lambda *_a: None)

    with pytest.raises(JSONRPCError) as exc:
        await endpoints.eth_call(
            **deps,
            transactions_processor=MagicMock(),
            params={"to": CONTRACT, "from": SENDER, "data": "0x00"},
        )

    receipt = exc.value.to_dict()["data"]["receipt"]
    assert "private_key" not in receipt["node_config"]


def _explorer_transaction():
    node_config = {"address": SENDER, "private_key": SECRET}
    return SimpleNamespace(
        hash="0xabc",
        status=SimpleNamespace(value="FINALIZED"),
        from_address=SENDER,
        to_address=CONTRACT,
        input_data=None,
        data=None,
        consensus_data={
            "leader_receipt": [{"node_config": dict(node_config)}],
            "validators": [{"node_config": dict(node_config)}],
        },
        nonce=0,
        value=0,
        type=2,
        gaslimit=None,
        created_at=None,
        leader_only=False,
        execution_mode=None,
        r=None,
        s=None,
        v=None,
        appeal_failed=0,
        consensus_history={
            "consensus_results": [
                {"leader_result": [{"node_config": dict(node_config)}]}
            ]
        },
        timestamp_appeal=None,
        appeal_processing_time=0,
        config_rotation_rounds=None,
        num_of_initial_validators=None,
        last_vote_timestamp=None,
        rotation_count=0,
        leader_timeout_validators=None,
        sim_config=None,
        triggered_by_hash=None,
        triggered_on=None,
        appealed=False,
        appeal_undetermined=False,
        appeal_leader_timeout=False,
        appeal_validators_timeout=False,
        timestamp_awaiting_finalization=None,
        blocked_at=None,
        worker_id=None,
    )


def test_explorer_serialize_tx_redacts_private_keys(monkeypatch):
    monkeypatch.delenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", raising=False)

    serialized = queries._serialize_tx(_explorer_transaction())

    assert serialized["consensus_data"]["leader_receipt"][0]["node_config"] == {
        "address": SENDER
    }
    assert serialized["consensus_data"]["validators"][0]["node_config"] == {
        "address": SENDER
    }
    assert serialized["consensus_history"]["consensus_results"][0]["leader_result"][0][
        "node_config"
    ] == {"address": SENDER}


def test_explorer_serialize_tx_can_show_private_keys_for_local_debug(monkeypatch):
    monkeypatch.setenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", "true")

    serialized = queries._serialize_tx(_explorer_transaction())

    assert (
        serialized["consensus_data"]["leader_receipt"][0]["node_config"]["private_key"]
        == SECRET
    )
