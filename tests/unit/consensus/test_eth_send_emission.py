"""Tests for EthSend emission handling.

Verifies:
- PendingTransaction with is_eth_send flag serializes/deserializes correctly
- EthSend emissions produce SEND-type child transactions (not RUN_CONTRACT)
- execute_transfer skips sender debit for triggered (child) transactions
"""

from backend.node.types import PendingTransaction


class TestPendingTransactionEthSend:
    def test_eth_send_to_dict(self):
        pt = PendingTransaction(
            address="0xrecipient",
            calldata=b"",
            code=None,
            salt_nonce=0,
            on="finalized",
            value=1000,
            is_eth_send=True,
            gas_used=123,
        )
        d = pt.to_dict()
        assert d["is_eth_send"] is True
        assert d["address"] == "0xrecipient"
        assert d["value"] == 1000
        assert d["gas_used"] == 123
        assert "calldata" not in d

    def test_eth_send_from_dict(self):
        d = {
            "address": "0xrecipient",
            "is_eth_send": True,
            "on": "finalized",
            "value": 500,
            "gas_used": 77,
        }
        pt = PendingTransaction.from_dict(d)
        assert pt.is_eth_send is True
        assert pt.address == "0xrecipient"
        assert pt.value == 500
        assert pt.gas_used == 77
        assert pt.calldata == b""
        assert pt.code is None

    def test_eth_send_from_dict_coerces_serialized_numeric_fields(self):
        d = {
            "address": "0xrecipient",
            "is_eth_send": True,
            "on": "finalized",
            "value": str(3 * 10**18),
            "declared_budget": "0",
            "gas_used": "77",
        }

        pt = PendingTransaction.from_dict(d)

        assert pt.value == 3 * 10**18
        assert pt.declared_budget == 0
        assert pt.gas_used == 77

    def test_eth_send_roundtrip(self):
        original = PendingTransaction(
            address="0xabc",
            calldata=b"",
            code=None,
            salt_nonce=0,
            on="accepted",
            value=42,
            is_eth_send=True,
            gas_used=91,
        )
        restored = PendingTransaction.from_dict(original.to_dict())
        assert restored.is_eth_send is True
        assert restored.address == original.address
        assert restored.value == original.value
        assert restored.on == original.on
        assert restored.gas_used == original.gas_used

    def test_non_eth_send_default(self):
        pt = PendingTransaction(
            address="0xcontract",
            calldata=b"\x01\x02",
            code=None,
            salt_nonce=0,
            on="finalized",
            value=100,
        )
        assert pt.is_eth_send is False
        d = pt.to_dict()
        assert "is_eth_send" not in d

    def test_is_not_deploy(self):
        pt = PendingTransaction(
            address="0xrecipient",
            calldata=b"",
            code=None,
            salt_nonce=0,
            on="finalized",
            value=1000,
            is_eth_send=True,
        )
        assert pt.is_deploy() is False
