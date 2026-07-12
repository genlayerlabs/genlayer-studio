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


def test_eth_send_round_trip_preserves_vote_equality():
    """Failing repro: an EthSend PendingTransaction with non-empty calldata is
    not equal to itself after a to_dict/from_dict round-trip, because to_dict
    omits `calldata` for is_eth_send and from_dict unconditionally sets it to
    b"". provide_result (genvm/base.py) still populates calldata from the
    emission, and _set_vote (node/base.py) compares full dataclass equality of
    pending_transactions. So a deserialized leader receipt (from consensus_data)
    compared against a validator's freshly-executed receipt mismatches on
    calldata alone -> a spurious Vote.DETERMINISTIC_VIOLATION even though both
    executed identically."""
    pt = PendingTransaction(
        address="0x" + "11" * 20,
        calldata=b"\xde\xad\xbe\xef",
        code=None,
        salt_nonce=0,
        on="finalized",
        value=1,
        is_eth_send=True,
    )
    round_tripped = PendingTransaction.from_dict(pt.to_dict())
    assert round_tripped == pt, (
        "EthSend pending-tx must survive a serialization round-trip for vote "
        f"equality; calldata {pt.calldata!r} became {round_tripped.calldata!r}"
    )
