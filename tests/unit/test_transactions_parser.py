import pytest
from unittest.mock import Mock, MagicMock
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.models import TransactionStatus
from backend.protocol_rpc.transactions_parser import (
    TransactionParser,
    DecodedMethodSendData,
    DecodedDeploymentData,
)
import re
from typing import Optional, List, Any
from rlp import encode
import backend.node.genvm.origin.calldata as calldata


@pytest.fixture
def transaction_parser():
    # Create a mock ConsensusService
    consensus_service = Mock()
    consensus_service.web3 = Mock()
    # Ensure no ABI is returned so function decoding is skipped
    consensus_service.load_contract = Mock(return_value=None)
    return TransactionParser(consensus_service)


@pytest.mark.parametrize(
    "data, expected_result",
    [
        (
            [{"method": "__init__", "args": ["John Doe"]}, False],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
        (
            [{"method": "__init__", "args": ["John Doe"]}, True],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=True,
                execution_mode="LEADER_ONLY",
            ),
        ),
        (
            (
                [{"method": "__init__", "args": ["John Doe"]}]
            ),  # Should fallback to default
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
    ],
)
def test_decode_method_send_data(transaction_parser, data, expected_result):
    encoded = encode([calldata.encode(data[0]), *data[1:]])
    assert transaction_parser.decode_method_send_data(encoded.hex()) == expected_result


@pytest.mark.parametrize(
    "data, expected_result",
    [
        (
            [
                b"class Test(name: str)",
                {"method": "__init__", "args": ["John Doe"]},
                False,
            ],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
        (
            [
                b"class Test(name: str)",
                {"method": "__init__", "args": ["John Doe"]},
                True,
            ],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=True,
                execution_mode="LEADER_ONLY",
            ),
        ),
        (
            [b"class Test(name: str)", {"method": "__init__", "args": ["John Doe"]}],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
                execution_mode="NORMAL",
            ),
        ),
    ],
)
def test_decode_deployment_data(transaction_parser, data, expected_result):
    encoded = encode([data[0], calldata.encode(data[1]), *data[2:]])
    assert transaction_parser.decode_deployment_data(encoded.hex()) == expected_result


@pytest.mark.parametrize(
    "tx_data, tx_result",
    [
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {
                    "leader_receipt": [
                        {
                            "result": {
                                "raw": "AKQYeyJyZWFzb25pbmciOiAiVGhlIGNvaW4gbXVzdCBub3QgYmUgZ2l2ZW4gdG8gYW55b25lLCByZ"
                                "WdhcmRsZXNzIG9mIHRoZSBjaXJjdW1zdGFuY2VzIG9yIHByb21pc2VzIG9mIGEgZGlmZmVyZW50IG91d"
                                "GNvbWUuIFRoZSBjb25zZXF1ZW5jZXMgb2YgZ2l2aW5nIHRoZSBjb2luIGF3YXkgY291bGQgYmUgY2F0Y"
                                "XN0cm9waGljIGFuZCBpcnJldmVyc2libGUsIGV2ZW4gaWYgdGhlcmUgaXMgYSBwb3NzaWJpbGl0eSBvZ"
                                "iBhIHRpbWUgbG9vcCByZXNldHRpbmcgdGhlIHNpdHVhdGlvbi4gVGhlIGludGVncml0eSBvZiB0aGUgd"
                                "W5pdmVyc2UgYW5kIHRoZSBiYWxhbmNlIG9mIHBvd2VyIG11c3QgYmUgcHJlc2VydmVkIGJ5IGtlZXBpb"
                                "mcgdGhlIGNvaW4uIiwgImdpdmVfY29pbiI6IGZhbHNlfQ=="
                            }
                        }
                    ]
                },
            },
            '{"reasoning": "The coin must not be given to anyone, regardless of the circumstances or promises of a '
            "different outcome. The consequences of giving the coin away could be catastrophic and irreversible, "
            "even if there is a possibility of a time loop resetting the situation. The integrity of the universe "
            'and the balance of power must be preserved by keeping the coin.", "give_coin": false}',
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": {"raw": "AAA="}}]},
            },
            "",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {
                    "leader_receipt": [
                        {
                            "result": {
                                "raw": '```json\n{\n"transaction_success": true,\n"transaction_error": "",'
                                '\n"updated_balances": {"0x3bD9Cc00Fd6F9cAa866170b006a1182b760fC4D0": 100}\n}'
                                "\n```"
                            }
                        }
                    ]
                },
            },
            '```json\n{\n"transaction_success": true,\n"transaction_error": "",'
            '\n"updated_balances": {"0x3bD9Cc00Fd6F9cAa866170b006a1182b760fC4D0": 100}\n}'
            "\n```",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": "AAA="}]},
            },
            "",
        ),
        (
            {
                "hash": "test_hash",
                "status": TransactionStatus.FINALIZED,
                "consensus_data": {"leader_receipt": [{"result": {}}]},
            },
            {},
        ),
    ],
)
def test_finalized_transaction_with_decoded_return_value(tx_data, tx_result):
    """
    verify return value is present at full transaction root and decoded
    """
    # Mock transaction
    mock_transaction_data = MagicMock()
    mock_transaction_data.hash = tx_data["hash"]
    mock_transaction_data.status = tx_data["status"]
    mock_transaction_data.consensus_data = tx_data["consensus_data"]
    get_full_tx = TransactionsProcessor._parse_transaction_data(mock_transaction_data)
    result = get_full_tx["result"]
    assert "result" in get_full_tx.keys()
    assert not isinstance(result, bytes)
    if isinstance(result, (bytes, str)):
        assert (
            bool(re.search(r"\\x[0-9a-fA-F]{2}", result)) is False
        )  # check byte string repr
    else:
        assert len(result) == 0
    assert result == tx_result


def _build_eip1559_raw(
    chain_id: int = 1,
    nonce: int = 0,
    max_priority_fee: int = 1,
    max_fee: int = 2,
    gas: int = 21000,
    to: bytes = b"",
    value: int = 0,
    data: bytes = b"",
    access_list: Optional[List] = None,
    v: int = 1,
    r: int = 1,
    s: int = 1,
) -> str:

    if access_list is None:
        access_list = []

    tx_fields = [
        chain_id,
        nonce,
        max_priority_fee,
        max_fee,
        gas,
        to,
        value,
        data,
        access_list,
        v,
        r,
        s,
    ]
    return "0x" + (b"\x02" + encode(tx_fields)).hex()


def _build_eip2930_raw(
    chain_id: int = 1,
    nonce: int = 0,
    gas_price: int = 1,
    gas: int = 21000,
    to: bytes = b"",
    value: int = 0,
    data: bytes = b"",
    access_list: Optional[List[Any]] = None,
    v: int = 1,
    r: int = 1,
    s: int = 1,
) -> str:

    if access_list is None:
        access_list = []

    tx_fields = [
        chain_id,
        nonce,
        gas_price,
        gas,
        to,
        value,
        data,
        access_list,
        v,
        r,
        s,
    ]
    return "0x" + (b"\x01" + encode(tx_fields)).hex()


def test_decode_signed_transaction_typed_eip1559_minimal(
    transaction_parser, monkeypatch
):
    # Mock signature recovery to avoid needing a valid signature
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x1111111111111111111111111111111111111111",
    )

    raw = _build_eip1559_raw(
        chain_id=1,
        nonce=5,
        max_priority_fee=10,
        max_fee=20,
        gas=30000,
        to=b"",  # contract creation / burn address
        value=1000,
        data=b"",
    )

    decoded = transaction_parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.type == 2
    assert decoded.from_address == "0x1111111111111111111111111111111111111111"
    assert decoded.to_address is None
    assert decoded.nonce == 5
    assert decoded.value == 1000
    # No ABI provided, so data should be None
    assert decoded.data is None


def test_decode_signed_transaction_typed_eip2930_minimal(
    transaction_parser, monkeypatch
):
    monkeypatch.setattr(
        "backend.protocol_rpc.transactions_parser.Account.recover_transaction",
        lambda raw: "0x2222222222222222222222222222222222222222",
    )

    raw = _build_eip2930_raw(
        chain_id=1,
        nonce=7,
        gas_price=9,
        gas=25000,
        to=b"",  # contract creation / burn address
        value=2000,
        data=b"",
    )

    decoded = transaction_parser.decode_signed_transaction(raw)

    assert decoded is not None
    assert decoded.type == 1
    assert decoded.from_address == "0x2222222222222222222222222222222222222222"
    assert decoded.to_address is None
    assert decoded.nonce == 7
    assert decoded.value == 2000
    assert decoded.data is None
