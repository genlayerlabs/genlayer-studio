"""Failing repros: eth_getTransactionReceipt / eth_getBlockByHash report wrong
values because the formatters read dict keys that _parse_transaction_data never
produces.

TransactionsProcessor.get_transaction_by_hash -> _parse_transaction_data returns
a dict with NO `block_number`, no top-level `contract_address`, no `gas_used`,
and a lifecycle-string `status` (e.g. "CANCELED"). The receipt/block formatters
in endpoints.py read `block_number`, `contract_address`, and treat `status` as a
success boolean, so:

  * receipt.status is always 0x1 (a non-empty status string is always truthy)
    -> a reverted / canceled / undetermined tx is reported as SUCCESS.
  * receipt.blockNumber / block.number / block.timestamp are always 0x0.
  * receipt.contractAddress is always null, even for a contract deployment
    (the deployed address lives in transaction["data"]["contract_address"]).

These are Ethereum JSON-RPC compatibility bugs (wallets read receipt.status ==
0x1 as "succeeded" and use blockNumber for confirmation counting). No fix here —
failing reproductions only.
"""

from backend.protocol_rpc import endpoints


BLOCK = 1234567
DEPLOYED = "0x" + "AB" * 20
TX_HASH = "0x" + "de" * 32


class _FakeTP:
    def __init__(self, tx):
        self._tx = tx

    def get_transaction_by_hash(self, transaction_hash, include_contract_snapshot=True):
        return self._tx


def _parsed_tx(**overrides):
    tx = {
        "hash": TX_HASH,
        "status": "FINALIZED",
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "data": {},
        "type": 1,
        "value": 0,
        "nonce": 0,
        "timestamp_awaiting_finalization": BLOCK,
        "consensus_data": None,
    }
    tx.update(overrides)
    return tx


def test_receipt_status_is_failure_for_canceled_transaction():
    # A CANCELED tx did not succeed; Ethereum receipt.status must be 0x0.
    tx = _parsed_tx(status="CANCELED")
    receipt = endpoints.get_transaction_receipt(_FakeTP(tx), TX_HASH)
    assert receipt["status"] == "0x0", (
        f"receipt.status for a CANCELED tx must be 0x0, got {receipt['status']} "
        "(the formatter treats the lifecycle status string as a success boolean)"
    )


def test_receipt_block_number_reflects_transaction_block():
    tx = _parsed_tx()
    receipt = endpoints.get_transaction_receipt(_FakeTP(tx), TX_HASH)
    assert receipt["blockNumber"] == hex(BLOCK), (
        f"receipt.blockNumber must be {hex(BLOCK)}, got {receipt['blockNumber']}"
    )


def test_receipt_contract_address_set_for_deploy():
    tx = _parsed_tx(data={"contract_address": DEPLOYED})
    receipt = endpoints.get_transaction_receipt(_FakeTP(tx), TX_HASH)
    assert receipt["contractAddress"] == DEPLOYED, (
        "receipt.contractAddress must be the deployed address for a deployment, "
        f"got {receipt['contractAddress']}"
    )


def test_block_by_hash_number_and_timestamp_are_not_zero():
    tx = _parsed_tx()
    block = endpoints.get_block_by_hash(_FakeTP(tx), TX_HASH, False)
    assert block["number"] == hex(BLOCK), (
        f"block.number must be {hex(BLOCK)}, got {block['number']}"
    )
    assert block["timestamp"] == hex(BLOCK), (
        f"block.timestamp must be {hex(BLOCK)}, got {block['timestamp']}"
    )
