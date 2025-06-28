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
from rlp import encode
import backend.node.genvm.origin.calldata as calldata


@pytest.fixture
def transaction_parser():
    # Create a mock ConsensusService
    consensus_service = Mock()
    consensus_service.web3 = Mock()
    return TransactionParser(consensus_service)


@pytest.mark.parametrize(
    "data, expected_result",
    [
        (
            [{"method": "__init__", "args": ["John Doe"]}, False],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
            ),
        ),
        (
            [{"method": "__init__", "args": ["John Doe"]}, True],
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=True,
            ),
        ),
        (
            (
                [{"method": "__init__", "args": ["John Doe"]}]
            ),  # Should fallback to default
            DecodedMethodSendData(
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
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
            ),
        ),
        (
            [b"class Test(name: str)", {"method": "__init__", "args": ["John Doe"]}],
            DecodedDeploymentData(
                contract_code=b"class Test(name: str)",
                calldata=b"\x16\x04args\rDJohn Doe\x06methodD__init__",
                leader_only=False,
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
