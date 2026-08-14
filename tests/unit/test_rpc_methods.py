from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.protocol_rpc import endpoints
from backend.protocol_rpc import rpc_methods
from backend.protocol_rpc.exceptions import JSONRPCError


def test_call_status_selector_accepts_decided_and_finalized():
    assert endpoints._state_status_from_call_params({"status": "decided"}) == "accepted"
    assert (
        endpoints._state_status_from_call_params({"status": "finalized"}) == "finalized"
    )


def test_call_status_selector_preserves_legacy_transaction_hash_variant():
    assert (
        endpoints._state_status_from_call_params(
            {"transaction_hash_variant": "latest-final"}
        )
        == "finalized"
    )
    assert (
        endpoints._state_status_from_call_params(
            {"transaction_hash_variant": "latest-nonfinal"}
        )
        == "accepted"
    )


def test_call_status_selector_rejects_node_legacy_accepted_value():
    with pytest.raises(JSONRPCError) as exc:
        endpoints._state_status_from_call_params({"status": "accepted"})

    assert exc.value.code == -32602
    assert "decided" in exc.value.message


def test_eth_get_transaction_by_hash_requests_no_contract_snapshot():
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = {"hash": "0xabc"}

    result = endpoints.get_transaction_by_hash(
        transactions_processor=transactions_processor,
        transaction_hash="0xabc",
    )

    assert result == {"hash": "0xabc"}
    transactions_processor.get_transaction_by_hash.assert_called_once_with(
        "0xabc", None, include_contract_snapshot=False
    )


def test_eth_get_transaction_by_hash_redacts_private_keys_by_default(monkeypatch):
    monkeypatch.delenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", raising=False)
    transactions_processor = MagicMock()
    stored_transaction = {
        "hash": "0xabc",
        "consensus_data": {
            "leader_receipt": [
                {
                    "node_config": {
                        "address": "0xvalidator",
                        "private_key": "0xsecret",
                    }
                }
            ],
            "validators": [
                {
                    "node_config": {
                        "address": "0xvalidator2",
                        "privateKey": "0xsecret2",
                    }
                }
            ],
        },
    }
    transactions_processor.get_transaction_by_hash.return_value = stored_transaction

    result = endpoints.get_transaction_by_hash(
        transactions_processor=transactions_processor,
        transaction_hash="0xabc",
    )

    leader_config = result["consensus_data"]["leader_receipt"][0]["node_config"]
    validator_config = result["consensus_data"]["validators"][0]["node_config"]
    assert leader_config == {"address": "0xvalidator"}
    assert validator_config == {"address": "0xvalidator2"}
    assert (
        stored_transaction["consensus_data"]["leader_receipt"][0]["node_config"][
            "private_key"
        ]
        == "0xsecret"
    )


def test_eth_get_transaction_by_hash_can_show_private_keys_for_local_debug(
    monkeypatch,
):
    monkeypatch.setenv("SHOW_VALIDATOR_PRIVATE_KEYS_IN_RPC", "true")
    transactions_processor = MagicMock()
    stored_transaction = {
        "hash": "0xabc",
        "consensus_data": {
            "leader_receipt": [
                {"node_config": {"address": "0xvalidator", "private_key": "0xsecret"}}
            ]
        },
    }
    transactions_processor.get_transaction_by_hash.return_value = stored_transaction

    result = endpoints.get_transaction_by_hash(
        transactions_processor=transactions_processor,
        transaction_hash="0xabc",
    )

    assert result == stored_transaction


def test_eth_transaction_receipt_requests_no_contract_snapshot():
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = {
        "hash": "0xabc",
        "from_address": "0x1111111111111111111111111111111111111111",
        "to_address": "0x2222222222222222222222222222222222222222",
        "status": "FINALIZED",
    }

    receipt = endpoints.get_transaction_receipt(transactions_processor, "0xabc")

    assert receipt["transactionHash"] == "0xabc"
    transactions_processor.get_transaction_by_hash.assert_called_once_with(
        "0xabc", include_contract_snapshot=False
    )


def test_eth_get_block_by_number_requests_no_contract_snapshot_for_full_tx():
    transactions_processor = MagicMock()
    transactions_processor.get_transactions_for_block.return_value = {
        "number": "0x1",
        "transactions": [{"hash": "0xabc"}],
    }

    result = endpoints.get_block_by_number(
        transactions_processor=transactions_processor,
        block_number="0x1",
        full_tx=True,
    )

    assert result == {"number": "0x1", "transactions": [{"hash": "0xabc"}]}
    transactions_processor.get_transactions_for_block.assert_called_once_with(
        1,
        include_full_tx=True,
        include_contract_snapshot=False,
    )


def test_eth_get_block_by_hash_requests_no_contract_snapshot():
    transactions_processor = MagicMock()
    transactions_processor.get_transaction_by_hash.return_value = {
        "hash": "0xabc",
        "block_number": 1,
        "timestamp": 2,
    }

    result = endpoints.get_block_by_hash(
        transactions_processor=transactions_processor,
        block_hash="0xabc",
        full_tx=True,
    )

    assert result["transactions"] == [
        {"hash": "0xabc", "block_number": 1, "timestamp": 2}
    ]
    transactions_processor.get_transaction_by_hash.assert_called_once_with(
        "0xabc", include_contract_snapshot=False
    )


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
